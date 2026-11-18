// Home / map view: color-coded camera markers on Google Maps.
// Marker colours (PDF thresholds, on the backend risk_score 0–1):
//   Green <40%, Yellow 40–70%, Red >70%.
// Markers come from GET /intersections + POST /predict_risk/batch
// (chunked at 100 items — the backend batch limit). Tapping a marker
// opens the detail sheet: daily prediction, 24-hour curve with the
// hourly bands, and the reasons from /predict_risk. Hourly values are
// labelled as estimates everywhere. The risk score shown is the
// backend's returned risk_score — the app invents no hourly scale.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../config.dart';
import '../services/cache_service.dart';
import '../services/map_style.dart';
import '../services/proximity_service.dart';
import '../services/risk.dart';
import '../services/settings_service.dart';
import '../services/sound_service.dart';
import '../services/subscription_service.dart';
import '../widgets/hourly_chart.dart';
import '../widgets/missing_key_view.dart';

class MapScreen extends StatefulWidget {
  const MapScreen({
    super.key,
    required this.settings,
    required this.client,
    required this.proximity,
    required this.sound,
    required this.subscription,
    required this.onBaseUrlChanged,
  });
  final AppSettings settings;
  final RedunatorApiClient client;
  final ProximityService proximity;
  final SoundService sound;
  final SubscriptionService subscription;
  final VoidCallback onBaseUrlChanged;

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  GoogleMapController? _mapController;
  List<Intersection> _cameras = [];
  Map<String, Prediction> _predictions = {};
  bool _loading = true;
  String? _error;
  bool _offline = false;
  String? _cacheAge;
  String? _alertMessage;

  static const LatLng _chicago = LatLng(41.8781, -87.6298);

  @override
  void initState() {
    super.initState();
    // If Pro is bought while the app is open (from Settings), start
    // the proximity watcher when the tab is already loaded; stop it
    // if Pro lapses.
    widget.subscription.addListener(_onSubscriptionChanged);
    _load();
  }

  @override
  void dispose() {
    widget.subscription.removeListener(_onSubscriptionChanged);
    super.dispose();
  }

  void _onSubscriptionChanged() {
    if (!mounted) return;
    if (widget.subscription.isPro) {
      if (_cameras.isNotEmpty && !widget.proximity.running) {
        _startProximity();
      }
    } else {
      unawaited(widget.proximity.stop());
      if (mounted) {
        setState(() {
          _alertMessage = null;
        });
      }
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _offline = false;
    });
    try {
      final cameras = await widget.client.getIntersections(limit: 300);
      final predictions = <String, Prediction>{};
      // Batch limit is 100 items — chunk the 300 cameras.
      for (var i = 0; i < cameras.length; i += 100) {
        final chunk = cameras.sublist(
          i,
          i + 100 > cameras.length ? cameras.length : i + 100,
        );
        final batch = await widget.client.predictRiskBatch(
          chunk
              .map((c) => {'camera_id': c.cameraId})
              .toList(),
        );
        for (final p in batch.results) {
          predictions[p.cameraId] = p;
        }
      }
      await CacheService().save(cameras, predictions);
      if (!mounted) return;
      setState(() {
        _cameras = cameras;
        _predictions = predictions;
        _loading = false;
      });
      _startProximity();
    } on ApiException catch (e) {
      await _loadFromCache('Backend error: ${e.message}');
    } catch (e) {
      await _loadFromCache('Could not reach the backend: $e');
    }
  }

  Future<void> _loadFromCache(String reason) async {
    final cached = await CacheService().load();
    if (!mounted) return;
    if (cached == null || cached.intersections.isEmpty) {
      setState(() {
        _loading = false;
        _error = '$reason\nNo cached data is available yet.';
      });
      return;
    }
    setState(() {
      _cameras = cached.intersections;
      _predictions = cached.predictions;
      _loading = false;
      _offline = true;
      _cacheAge = cached.ageLabel;
      _error = reason;
    });
    _startProximity();
  }

  Future<void> _startProximity() async {
    // Proximity alerts are a Pro feature. Free users see a note on
    // the Settings screen instead of a silently dead watcher.
    if (!widget.subscription.isPro) return;
    await widget.proximity.start(
      cameras: _cameras,
      predictions: _predictions,
      alertDistanceM: widget.settings.alertDistanceM,
      riskThresholdScore: widget.settings.riskThresholdScore,
      onAlert: (cam, dist) {
        if (!mounted) return;
        setState(() {
          _alertMessage =
              'High-risk camera ahead: ${cam.intersection} — ${widget.settings.formatDistance(dist)} away';
        });
      },
    );
  }

  Set<Marker> get _markers {
    return _cameras.map((cam) {
      if (cam.latitude == null || cam.longitude == null) {
        return null;
      }
      final pred = _predictions[cam.cameraId];
      final score = pred?.riskScore ?? cam.typicalWeekdayScore;
      return Marker(
        markerId: MarkerId(cam.cameraId),
        position: LatLng(cam.latitude!, cam.longitude!),
        icon: BitmapDescriptor.defaultMarkerWithHue(
          markerHueForScore(score),
        ),
        infoWindow: InfoWindow(
          title: cam.intersection,
          snippet:
              'Risk ${formatPercent(score)} · ${pred?.riskLevel ?? cam.typicalWeekdayBand}',
        ),
        onTap: () {
          unawaited(widget.sound.playTap());
          _showDetail(cam);
        },
      );
    }).whereType<Marker>().toSet();
  }

  Future<void> _showDetail(Intersection cam) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => IntersectionDetailSheet(
        camera: cam,
        prediction: _predictions[cam.cameraId],
        client: widget.client,
        settings: widget.settings,
        sound: widget.sound,
      ),
    );
  }

  Future<void> _goToMyLocation() async {
    final pos = await widget.proximity.currentPosition();
    if (pos == null) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Location permission denied — enable it in system settings to use “My location”.',
          ),
        ),
      );
      return;
    }
    await _mapController?.animateCamera(
      CameraUpdate.newLatLngZoom(LatLng(pos.latitude, pos.longitude), 15),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!hasMapsApiKey) return const MissingKeyView();
    return Stack(
      children: [
        if (_loading)
          const Center(child: CircularProgressIndicator())
        else if (_cameras.isEmpty)
          Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.cloud_off, size: 48),
                  const SizedBox(height: 12),
                  Text(_error ?? 'No camera data.'),
                  const SizedBox(height: 12),
                  ElevatedButton(
                    onPressed: () {
                      unawaited(widget.sound.playTap());
                      _load();
                    },
                    child: const Text('Retry'),
                  ),
                ],
              ),
            ),
          )
        else
          GoogleMap(
            initialCameraPosition: const CameraPosition(
              target: _chicago,
              zoom: 11,
            ),
            markers: _markers,
            myLocationEnabled: true,
            myLocationButtonEnabled: false,
            // Dark map style follows the app theme (system/light/dark
            // setting). Rebuilds when the setting changes because the
            // MaterialApp above rebuilds on AppSettings notifications.
            style: widget.settings.isDark(context) ? darkMapStyleJson : null,
            onMapCreated: (c) => _mapController = c,
          ),
        Positioned(
          top: 8,
          left: 8,
          right: 8,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_offline)
                _Banner(
                  'Offline — showing cached data (${_cacheAge ?? 'unknown age'}). '
                  'Risk values may be out of date.',
                  Colors.orange.shade800,
                ),
              if (_error != null && !_offline)
                _Banner(_error!, Colors.red.shade700),
              if (_alertMessage != null)
                _Banner(_alertMessage!, Colors.red.shade700),
            ],
          ),
        ),
        Positioned(
          left: 8,
          bottom: 16,
          child: _LegendCard(settings: widget.settings),
        ),
        Positioned(
          right: 8,
          bottom: 16,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              FloatingActionButton.small(
                heroTag: 'loc',
                onPressed: () {
                  unawaited(widget.sound.playTap());
                  _goToMyLocation();
                },
                tooltip: 'My location',
                child: const Icon(Icons.my_location),
              ),
              const SizedBox(height: 8),
              FloatingActionButton.small(
                heroTag: 'refresh',
                onPressed: () {
                  unawaited(widget.sound.playTap());
                  _load();
                },
                tooltip: 'Refresh',
                child: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner(this.text, this.color);
  final String text;
  final Color color;
  @override
  Widget build(BuildContext context) => Card(
        color: color,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Text(
            text,
            style: const TextStyle(color: Colors.white, fontSize: 13),
          ),
        ),
      );
}

class _LegendCard extends StatelessWidget {
  const _LegendCard({required this.settings});
  final AppSettings settings;
  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: const [
              Text('Camera risk', style: TextStyle(fontWeight: FontWeight.bold)),
              _LegendRow(Colors.green, 'Low <40%'),
              _LegendRow(Colors.amber, 'Medium 40–70%'),
              _LegendRow(Colors.red, 'High >70%'),
            ],
          ),
        ),
      );
}

class _LegendRow extends StatelessWidget {
  const _LegendRow(this.color, this.label);
  final Color color;
  final String label;
  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 10, height: 10, color: color),
          const SizedBox(width: 6),
          Text(label, style: const TextStyle(fontSize: 12)),
        ],
      );
}

/// Detail sheet for one camera: daily prediction, 24-hour curve with
/// hourly bands, reasons, recent daily trends, and Report Spike.
class IntersectionDetailSheet extends StatefulWidget {
  const IntersectionDetailSheet({
    super.key,
    required this.camera,
    required this.prediction,
    required this.client,
    required this.settings,
    required this.sound,
  });
  final Intersection camera;
  final Prediction? prediction;
  final RedunatorApiClient client;
  final AppSettings settings;
  final SoundService sound;

  @override
  State<IntersectionDetailSheet> createState() =>
      _IntersectionDetailSheetState();
}

class _IntersectionDetailSheetState extends State<IntersectionDetailSheet> {
  Prediction? _prediction;
  ViolationTrends? _trends;
  String? _trendsError;
  bool _reporting = false;
  int? _selectedHour;

  @override
  void initState() {
    super.initState();
    _prediction = widget.prediction;
    _loadDetails();
  }

  Future<void> _loadDetails() async {
    try {
      _prediction ??= await widget.client.predictRisk(
        cameraId: widget.camera.cameraId,
      );
    } catch (_) {
      // Keep the typical values from /intersections if the call fails.
    }
    try {
      final trends = await widget.client.getViolationTrends(
        cameraId: widget.camera.cameraId,
      );
      if (mounted) setState(() => _trends = trends);
    } on ApiException catch (e) {
      if (mounted) setState(() => _trendsError = e.message);
    } catch (_) {
      // Trends are a nice-to-have; the sheet works without them.
    }
    if (mounted) setState(() {});
  }

  Future<void> _reportSpike() async {
    setState(() => _reporting = true);
    try {
      final report = await widget.client.reportSpike(
        cameraId: widget.camera.cameraId,
        latitude: widget.camera.latitude,
        longitude: widget.camera.longitude,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Spike reported — thank you. Report #${report.id} recorded.',
          ),
        ),
      );
    } on ApiException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not send report: ${e.message}')),
      );
    } finally {
      if (mounted) setState(() => _reporting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cam = widget.camera;
    final pred = _prediction;
    final score = pred?.riskScore ?? cam.typicalWeekdayScore;
    final daily = pred?.dailyPrediction ?? cam.typicalWeekdayPred;
    final band = pred?.riskLevel ?? cam.typicalWeekdayBand;
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.75,
      minChildSize: 0.4,
      maxChildSize: 0.95,
      builder: (context, scrollController) => ListView(
        controller: scrollController,
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            cam.intersection,
            style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 4),
          Text('Camera ${cam.cameraId}'),
          const SizedBox(height: 12),
          Row(
            children: [
              Chip(
                label: Text('Risk ${formatPercent(score)} · $band'),
                backgroundColor: markerColorForScore(score).withValues(alpha: 0.2),
              ),
              const SizedBox(width: 8),
              if (pred != null)
                Chip(label: Text('Source: ${pred.dailySource}')),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Predicted violations today: ${daily.toStringAsFixed(1)} '
            '(daily band: LOW <4, MEDIUM 4–<10, HIGH ≥10/day).',
          ),
          if (pred != null) ...[
            const SizedBox(height: 8),
            Text(
              'Confidence: ${(pred.confidence * 100).round()}% '
              '(heuristic, not a calibrated probability). '
              'Model v${pred.modelVersion ?? '?'} · ${pred.date}.',
            ),
          ],
          const SizedBox(height: 16),
          const Text(
            'Hourly curve (estimates)',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          if (pred != null && pred.hourly.isNotEmpty)
            HourlyChart(
              hourly: pred.hourly,
              selectedHour: _selectedHour,
              onHourSelected: (h) => setState(() => _selectedHour = h),
            )
          else
            const Text('Hourly estimates unavailable for this camera.'),
          if (pred != null && _selectedHour != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                _hourLabel(pred.hourEntry(_selectedHour!)),
              ),
            ),
          if (pred != null && pred.reasons.isNotEmpty) ...[
            const SizedBox(height: 16),
            const Text(
              'Why this risk',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
            ),
            ...pred.reasons.map((r) => ListTile(
                  leading: const Icon(Icons.info_outline, size: 18),
                  title: Text(r),
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                )),
          ],
          if (_trends != null) ...[
            const SizedBox(height: 8),
            const Text(
              'Recent daily trend (observed)',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
            ),
            Text(
              '${_trends!.nDays} days (${_trends!.startDate} → ${_trends!.endDate}): '
              'mean observed ${_trends!.meanObserved?.toStringAsFixed(1) ?? '—'}/day, '
              'mean predicted ${_trends!.meanPredicted?.toStringAsFixed(1) ?? '—'}/day, '
              'total observed ${_trends!.totalObserved?.toStringAsFixed(0) ?? '—'}.',
            ),
          ] else if (_trendsError != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('Trends unavailable: $_trendsError'),
            ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              onPressed: _reporting
                  ? null
                  : () {
                      unawaited(widget.sound.playTap());
                      _reportSpike();
                    },
              icon: const Icon(Icons.report),
              label: Text(_reporting ? 'Sending…' : 'Report Spike here'),
            ),
          ),
          const SizedBox(height: 8),
          const Text(
            'Reporting a spike tells Redunator that enforcement is '
            'active at this camera right now.',
            style: TextStyle(fontSize: 12, color: Colors.grey),
          ),
        ],
      ),
    );
  }

  String _hourLabel(HourlyEntry? e) {
    if (e == null) return '';
    return '${e.hour.toString().padLeft(2, '0')}:00 estimate: '
        '${e.predictedViolations.toStringAsFixed(2)} violations/hour '
        '(${e.band}). Hourly values are estimates from proxies — '
        'not observed hourly violations.';
  }
}
