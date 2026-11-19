// Saved intersections (Pro): the cameras the user saved from the
// detail sheet, each with today's predicted risk and a SPIKE flag when
// today is >1.5x the camera's 7-day mean observed violations (see
// trend_analysis.dart for the rule). Non-Pro users see the upsell
// card. Data comes from the existing POST /predict_risk/batch and
// GET /violation_trends endpoints — no new backend calls.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import '../api/client.dart';
import '../api/models.dart';
import '../services/saved_service.dart';
import '../services/settings_service.dart';
import '../services/sound_service.dart';
import '../services/subscription_service.dart';
import '../services/trend_analysis.dart';
import '../widgets/pro_upsell.dart';
import 'map_screen.dart';

class SavedScreen extends StatelessWidget {
  const SavedScreen({
    super.key,
    required this.client,
    required this.settings,
    required this.sound,
    required this.subscription,
    required this.saved,
  });

  final RedunatorApiClient client;
  final AppSettings settings;
  final SoundService sound;
  final SubscriptionService subscription;
  final SavedService saved;

  @override
  Widget build(BuildContext context) {
    // Saved intersections are a Pro feature: non-Pro users get the
    // upsell card instead of the list. AnimatedBuilder rebuilds this
    // when a purchase completes (isPro flips).
    return AnimatedBuilder(
      animation: subscription,
      builder: (context, _) {
        if (!subscription.isPro) {
          return ListView(
            children: [ProUpsellCard(subscription: subscription, sound: sound)],
          );
        }
        return _SavedListView(
          client: client,
          settings: settings,
          sound: sound,
          subscription: subscription,
          saved: saved,
        );
      },
    );
  }
}

class _SavedRow {
  _SavedRow({required this.saved, this.prediction, this.mean7d});

  final SavedIntersection saved;
  final Prediction? prediction;

  /// Mean observed daily violations over the last 7 days of trend
  /// data, or null when trends were unavailable.
  final double? mean7d;

  /// The spike rule (see trend_analysis.dart): today's predicted
  /// daily violations are >1.5x the 7-day mean observed violations.
  bool get hasSpike =>
      prediction != null && isSpike(prediction!.dailyPrediction, mean7d);
}

class _SavedListView extends StatefulWidget {
  const _SavedListView({
    required this.client,
    required this.settings,
    required this.sound,
    required this.subscription,
    required this.saved,
  });

  final RedunatorApiClient client;
  final AppSettings settings;
  final SoundService sound;
  final SubscriptionService subscription;
  final SavedService saved;

  @override
  State<_SavedListView> createState() => _SavedListViewState();
}

class _SavedListViewState extends State<_SavedListView> {
  List<_SavedRow> _rows = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.saved.addListener(_reload);
    _load();
  }

  @override
  void dispose() {
    widget.saved.removeListener(_reload);
    super.dispose();
  }

  void _reload() {
    if (mounted) _load();
  }

  Future<void> _load() async {
    final saved = widget.saved.saved;
    if (saved.isEmpty) {
      if (!mounted) return;
      setState(() {
        _rows = [];
        _loading = false;
        _error = null;
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    // Today's predictions for every saved camera in one batch call.
    final predictions = <String, Prediction>{};
    try {
      final batch = await widget.client.predictRiskBatch(
        saved.map((s) => {'camera_id': s.cameraId}).toList(),
      );
      for (final p in batch.results) {
        predictions[p.cameraId] = p;
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load today’s risk: $e';
        _loading = false;
      });
    }
    // 7-day mean observed violations per camera, from the default
    // 90-day trends window (sliced locally — no date math, so it
    // cannot 404 on the data lag).
    final means = <String, double?>{};
    await Future.wait(
      saved.map((s) async {
        try {
          final trends = await widget.client.getViolationTrends(
            cameraId: s.cameraId,
          );
          means[s.cameraId] = meanObservedWindow(trends.days, 7);
        } catch (_) {
          means[s.cameraId] = null;
        }
      }),
    );
    if (!mounted) return;
    setState(() {
      _rows = saved
          .map(
            (s) => _SavedRow(
              saved: s,
              prediction: predictions[s.cameraId],
              mean7d: means[s.cameraId],
            ),
          )
          .toList();
      _loading = false;
    });
  }

  Future<void> _showDetail(_SavedRow row) async {
    final pred = row.prediction;
    final cam = Intersection(
      cameraId: row.saved.cameraId,
      intersection: row.saved.intersection,
      latitude: row.saved.latitude,
      longitude: row.saved.longitude,
      typicalWeekdayPred: pred?.dailyPrediction ?? 0,
      typicalWeekendPred: pred?.dailyPrediction ?? 0,
      typicalWeekdayBand: pred?.riskLevel ?? '',
      typicalWeekendBand: pred?.riskLevel ?? '',
      typicalWeekdayScore: pred?.riskScore ?? 0,
      typicalWeekendScore: pred?.riskScore ?? 0,
      actualTestMean: null,
      camMeanPreTest: null,
    );
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => IntersectionDetailSheet(
        camera: cam,
        prediction: pred,
        client: widget.client,
        settings: widget.settings,
        sound: widget.sound,
        subscription: widget.subscription,
        saved: widget.saved,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Saved intersections',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                ),
              ),
              IconButton(
                tooltip: 'Refresh',
                icon: const Icon(Icons.refresh),
                onPressed: () {
                  unawaited(widget.sound.playTap());
                  _load();
                },
              ),
            ],
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(_error!, style: const TextStyle(color: Colors.red)),
            ),
          if (_rows.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 24),
              child: Text(
                'No saved intersections yet.\n\nTap a camera on the '
                'Map tab and save it — it will show up here with '
                'today’s risk and a spike flag when today looks much '
                'worse than its recent normal.',
                textAlign: TextAlign.center,
              ),
            ),
          ..._rows.map((row) {
            final pred = row.prediction;
            return Card(
              child: ListTile(
                leading: Icon(
                  row.hasSpike ? Icons.warning_amber : Icons.bookmark,
                  color: row.hasSpike
                      ? Colors.red
                      : (pred == null
                            ? Colors.grey
                            : _riskColor(pred.riskScore)),
                ),
                title: Text(row.saved.intersection),
                subtitle: Text(
                  pred == null
                      ? 'Today’s risk unavailable.'
                      : 'Today: ${pred.dailyPrediction.toStringAsFixed(1)}/day '
                            '(${pred.riskLevel})'
                            '${row.mean7d == null ? '' : ' · 7-day mean ${row.mean7d!.toStringAsFixed(1)}/day'}',
                ),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (row.hasSpike)
                      const Chip(
                        label: Text('SPIKE'),
                        backgroundColor: Colors.red,
                        labelStyle: TextStyle(color: Colors.white),
                      ),
                    IconButton(
                      tooltip: 'Remove from saved',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () async {
                        unawaited(widget.sound.playTap());
                        await widget.saved.unsave(row.saved.cameraId);
                      },
                    ),
                  ],
                ),
                onTap: () {
                  unawaited(widget.sound.playTap());
                  _showDetail(row);
                },
              ),
            );
          }),
          const SizedBox(height: 8),
          const Text(
            'A spike means today’s predicted violations are more than '
            '1.5x this camera’s mean observed violations over the last '
            '7 days of city data.',
            style: TextStyle(fontSize: 12, color: Colors.grey),
          ),
        ],
      ),
    );
  }

  Color _riskColor(double score) {
    if (score < 0.4) return Colors.green;
    if (score <= 0.7) return Colors.amber;
    return Colors.red;
  }
}
