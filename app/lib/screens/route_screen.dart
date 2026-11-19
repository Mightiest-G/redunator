// Active route navigation screen: pick origin + destination, get a
// route, and see the route line coloured by camera risk along it.
//
// ROUTING SOURCE: OSRM (router.project-osrm.org) — free, no key, no
// account (see lib/api/routing.dart). Google Maps Directions is the
// fallback when OSRM fails and a Maps key is configured.
// Risk colouring: the route polyline is split into segments; each
// segment takes the colour of the highest-risk camera within 300 m
// of it (green <40%, yellow 40–70%, red >70% — same thresholds as
// the map). Cameras near the route are listed below the map.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../api/routing.dart';
import '../config.dart';
import '../services/proximity_service.dart';
import '../services/risk.dart';
import '../services/route_risk.dart';
import '../services/sound_service.dart';
import '../services/subscription_service.dart';
import '../widgets/missing_key_view.dart';
import '../widgets/pro_upsell.dart';

class RouteScreen extends StatefulWidget {
  const RouteScreen({
    super.key,
    required this.client,
    required this.proximity,
    required this.sound,
    required this.subscription,
  });
  final RedunatorApiClient client;
  final ProximityService proximity;
  final SoundService sound;
  final SubscriptionService subscription;

  @override
  State<RouteScreen> createState() => _RouteScreenState();
}

class _RouteScreenState extends State<RouteScreen> {
  List<Intersection> _cameras = [];
  Intersection? _origin;
  Intersection? _destination;
  RouteResult? _route;
  List<_NearCamera> _nearCameras = [];
  SafestDeparture? _safest;
  bool _safestLoading = false;
  bool _loading = false;
  bool _loadingList = true;
  String? _error;
  GoogleMapController? _mapController;

  static const LatLng _chicago = LatLng(41.8781, -87.6298);

  @override
  void initState() {
    super.initState();
    _loadCameras();
  }

  Future<void> _loadCameras() async {
    try {
      final list = await widget.client.getIntersections(limit: 300);
      if (!mounted) return;
      setState(() {
        _cameras = list;
        _loadingList = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingList = false;
        _error = 'Could not load intersections: $e';
      });
    }
  }

  Future<void> _useMyLocationAsOrigin() async {
    final pos = await widget.proximity.currentPosition();
    if (pos == null) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Location permission denied — pick an origin instead.'),
        ),
      );
      return;
    }
    setState(() {
      _origin = Intersection(
        cameraId: 'my-location',
        intersection:
            'My location (${pos.latitude.toStringAsFixed(4)}, ${pos.longitude.toStringAsFixed(4)})',
        latitude: pos.latitude,
        longitude: pos.longitude,
        typicalWeekdayPred: 0,
        typicalWeekendPred: 0,
        typicalWeekdayBand: '',
        typicalWeekendBand: '',
        typicalWeekdayScore: 0,
        typicalWeekendScore: 0,
        actualTestMean: null,
        camMeanPreTest: null,
      );
    });
  }

  Future<void> _computeRoute() async {
    final from = _origin;
    final to = _destination;
    if (from == null || to == null) return;
    if (from.latitude == null || to.latitude == null) return;
    setState(() {
      _loading = true;
      _error = null;
      _route = null;
      _nearCameras = [];
      _safest = null;
      _safestLoading = false;
    });
    try {
      final service = RouteService(googleMapsApiKey: googleMapsApiKey);
      final route = await service.getRoute(
        LatLngPoint(from.latitude!, from.longitude!),
        LatLngPoint(to.latitude!, to.longitude!),
      );
      final near = _camerasNearRoute(route);
      if (!mounted) return;
      setState(() {
        _route = route;
        _nearCameras = near;
        _loading = false;
      });
      await _fitRoute(route);
      // Safest time to leave (Pro): hourly predictions for the
      // cameras near this route, summed per hour of day.
      unawaited(_loadSafestDeparture());
    } on RoutingException catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.message;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = 'Routing failed: $e';
      });
    }
  }

  /// Cameras within 300 m of any sampled route point, with their
  /// typical weekday risk score, sorted by score descending.
  List<_NearCamera> _camerasNearRoute(RouteResult route) {
    final found = <String, _NearCamera>{};
    final step = (route.points.length / 100).ceil().clamp(1, 1000);
    for (var i = 0; i < route.points.length; i += step) {
      final p = route.points[i];
      for (final cam in _cameras) {
        if (cam.latitude == null || cam.longitude == null) continue;
        if (found.containsKey(cam.cameraId)) continue;
        final d = RouteService.distanceM(
          p,
          LatLngPoint(cam.latitude!, cam.longitude!),
        );
        if (d <= 300) {
          found[cam.cameraId] = _NearCamera(cam, d);
        }
      }
    }
    final list = found.values.toList()
      ..sort(
        (a, b) => b.camera.typicalWeekdayScore.compareTo(
          a.camera.typicalWeekdayScore,
        ),
      );
    return list;
  }

  /// Safest time to leave (Pro): one batch prediction for the cameras
  /// near the route, then the hour with the lowest summed hourly
  /// violations across them. Uses only data the app already fetches
  /// (POST /predict_risk/batch). When the batch fails, the card is
  /// simply hidden — the route itself still works.
  Future<void> _loadSafestDeparture() async {
    if (_nearCameras.isEmpty) return;
    setState(() => _safestLoading = true);
    try {
      final batch = await widget.client.predictRiskBatch(
        _nearCameras.map((nc) => {'camera_id': nc.camera.cameraId}).toList(),
      );
      final preds = <String, Prediction>{
        for (final p in batch.results) p.cameraId: p,
      };
      if (!mounted) return;
      setState(() {
        _safest = safestDepartureHour(
          predictions: preds,
          cameraIds: _nearCameras.map((nc) => nc.camera.cameraId).toList(),
          currentHour: DateTime.now().hour,
        );
        _safestLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _safestLoading = false);
    }
  }

  Future<void> _fitRoute(RouteResult route) async {
    if (route.points.isEmpty) return;
    var minLat = route.points.first.latitude;
    var maxLat = minLat;
    var minLng = route.points.first.longitude;
    var maxLng = minLng;
    for (final p in route.points) {
      if (p.latitude < minLat) minLat = p.latitude;
      if (p.latitude > maxLat) maxLat = p.latitude;
      if (p.longitude < minLng) minLng = p.longitude;
      if (p.longitude > maxLng) maxLng = p.longitude;
    }
    await _mapController?.animateCamera(
      CameraUpdate.newLatLngBounds(
        LatLngBounds(
          southwest: LatLng(minLat, minLng),
          northeast: LatLng(maxLat, maxLng),
        ),
        60,
      ),
    );
  }

  /// Split the route into coloured polylines: each chunk of points
  /// takes the colour of the highest-risk camera within 300 m of it.
  List<Polyline> get _routePolylines {
    final route = _route;
    if (route == null) return [];
    const chunk = 10;
    final lines = <Polyline>[];
    for (var i = 0; i < route.points.length - 1; i += chunk) {
      final end = (i + chunk).clamp(0, route.points.length - 1);
      final segment = route.points.sublist(i, end + 1);
      var worst = 0.0;
      for (final p in segment) {
        for (final cam in _cameras) {
          if (cam.latitude == null || cam.longitude == null) continue;
          if (RouteService.distanceM(
                p,
                LatLngPoint(cam.latitude!, cam.longitude!),
              ) <=
              300) {
            if (cam.typicalWeekdayScore > worst) {
              worst = cam.typicalWeekdayScore;
            }
          }
        }
      }
      lines.add(
        Polyline(
          polylineId: PolylineId('seg_$i'),
          points: segment.map((p) => LatLng(p.latitude, p.longitude)).toList(),
          color: worst == 0 ? Colors.blue : markerColorForScore(worst),
          width: 6,
        ),
      );
    }
    return lines;
  }

  Set<Marker> get _routeMarkers {
    final markers = <Marker>{};
    for (final nc in _nearCameras) {
      final cam = nc.camera;
      if (cam.latitude == null || cam.longitude == null) continue;
      markers.add(
        Marker(
          markerId: MarkerId('route_${cam.cameraId}'),
          position: LatLng(cam.latitude!, cam.longitude!),
          icon: BitmapDescriptor.defaultMarkerWithHue(
            markerHueForScore(cam.typicalWeekdayScore),
          ),
          infoWindow: InfoWindow(
            title: cam.intersection,
            snippet:
                'Typical risk ${formatPercent(cam.typicalWeekdayScore)} · ${nc.distanceM.round()} m from route',
          ),
        ),
      );
    }
    return markers;
  }

  @override
  Widget build(BuildContext context) {
    if (!hasMapsApiKey) return const MissingKeyView();
    // Route planning is a Pro feature: non-Pro users get the upsell
    // card instead of the form. AnimatedBuilder rebuilds this when a
    // purchase completes (isPro flips).
    return AnimatedBuilder(
      animation: widget.subscription,
      builder: (context, _) {
        if (!widget.subscription.isPro) {
          return ListView(
            children: [
              ProUpsellCard(
                subscription: widget.subscription,
                sound: widget.sound,
              ),
            ],
          );
        }
        return _buildRouteUi();
      },
    );
  }

  Widget _buildRouteUi() {
    final route = _route;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            children: [
              Row(
                children: [
                  Expanded(
                    child: _CameraDropdown(
                      label: 'Origin',
                      cameras: _cameras,
                      value: _origin,
                      onChanged: (c) => setState(() => _origin = c),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Use my location as origin',
                    icon: const Icon(Icons.my_location),
                    onPressed: () {
                      unawaited(widget.sound.playTap());
                      _useMyLocationAsOrigin();
                    },
                  ),
                ],
              ),
              const SizedBox(height: 8),
              _CameraDropdown(
                label: 'Destination',
                cameras: _cameras,
                value: _destination,
                onChanged: (c) => setState(() => _destination = c),
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed:
                      (_origin != null && _destination != null && !_loading)
                      ? () {
                          unawaited(widget.sound.playTap());
                          _computeRoute();
                        }
                      : null,
                  icon: const Icon(Icons.route),
                  label: Text(_loading ? 'Routing…' : 'Show route'),
                ),
              ),
              if (_loadingList)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: LinearProgressIndicator(),
                ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    _error!,
                    style: const TextStyle(color: Colors.red),
                  ),
                ),
              if (route != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Route via ${route.source}: '
                    '${(route.distanceM / 1000).toStringAsFixed(1)} km, '
                    'about ${(route.durationS / 60).round()} min. '
                    '${_nearCameras.length} camera(s) within 300 m of the route. '
                    'Segments are coloured by the highest-risk nearby camera '
                    '(green <40%, yellow 40–70%, red >70%).',
                  ),
                ),
              if (_safestLoading)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: LinearProgressIndicator(),
                ),
              if (_safest != null) _SafestDepartureCard(safest: _safest!),
            ],
          ),
        ),
        Expanded(
          child: GoogleMap(
            initialCameraPosition: const CameraPosition(
              target: _chicago,
              zoom: 11,
            ),
            polylines: _routePolylines.toSet(),
            markers: _routeMarkers,
            onMapCreated: (c) => _mapController = c,
          ),
        ),
        if (_nearCameras.isNotEmpty)
          SizedBox(
            height: 140,
            child: ListView.builder(
              itemCount: _nearCameras.length,
              itemBuilder: (context, i) {
                final nc = _nearCameras[i];
                return ListTile(
                  dense: true,
                  leading: Icon(
                    Icons.warning_amber,
                    color: markerColorForScore(nc.camera.typicalWeekdayScore),
                  ),
                  title: Text(nc.camera.intersection),
                  subtitle: Text(
                    'Typical risk ${formatPercent(nc.camera.typicalWeekdayScore)} '
                    '· ${nc.distanceM.round()} m from route',
                  ),
                );
              },
            ),
          ),
      ],
    );
  }
}

class _NearCamera {
  _NearCamera(this.camera, this.distanceM);
  final Intersection camera;
  final double distanceM;
}

/// Safest-time-to-leave card (Pro): the departure hour with the
/// lowest summed hourly violations across the route's cameras, compared
/// against right now. Hourly values are estimates — the card says so.
class _SafestDepartureCard extends StatelessWidget {
  const _SafestDepartureCard({required this.safest});

  final SafestDeparture safest;

  String get _hourLabel => '${safest.hour.toString().padLeft(2, '0')}:00';

  @override
  Widget build(BuildContext context) {
    final nowHour = DateTime.now().hour;
    final isNow = safest.hour == nowHour;
    final title = isNow
        ? 'Right now is the safest time to leave'
        : 'Safest to leave at $_hourLabel';
    return Card(
      margin: const EdgeInsets.only(top: 8),
      color: Theme.of(context).colorScheme.primaryContainer,
      child: ListTile(
        leading: const Icon(Icons.schedule),
        title: Text(title),
        subtitle: Text(
          isNow
              ? 'About ${safest.safestTotal.toStringAsFixed(1)} '
                    'violations/hour across the route’s '
                    '${safest.cameraCount} camera(s).'
              : 'About ${safest.safestTotal.toStringAsFixed(1)} '
                    'violations/hour across the route’s '
                    '${safest.cameraCount} camera(s), vs '
                    '${safest.nowTotal.toStringAsFixed(1)} now.\n'
                    'Hourly values are estimates from proxies.',
        ),
      ),
    );
  }
}

class _CameraDropdown extends StatelessWidget {
  const _CameraDropdown({
    required this.label,
    required this.cameras,
    required this.value,
    required this.onChanged,
  });
  final String label;
  final List<Intersection> cameras;
  final Intersection? value;
  final ValueChanged<Intersection?> onChanged;

  @override
  Widget build(BuildContext context) {
    final items = List<Intersection>.from(cameras);
    final current = value;
    if (current != null && !items.any((c) => c.cameraId == current.cameraId)) {
      items.insert(0, current);
    }
    return DropdownButtonFormField<Intersection>(
      initialValue: current,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
        isDense: true,
      ),
      isExpanded: true,
      items: items
          .map(
            (c) => DropdownMenuItem<Intersection>(
              value: c,
              child: Text(c.intersection, overflow: TextOverflow.ellipsis),
            ),
          )
          .toList(),
      onChanged: onChanged,
    );
  }
}
