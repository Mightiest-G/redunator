// Weekly risk report (Exclusive): one week in review for every
// saved intersection — this week's mean vs the prior week's, the
// worst day, and how many days spiked. Built from the same
// GET /violation_trends data the trend section uses; no new backend
// calls. The city feed lags ~2 weeks, so "this week" is the most
// recent week of data, and the screen says so.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import '../api/client.dart';
import '../services/saved_service.dart';
import '../services/sound_service.dart';
import '../services/weekly_report.dart';

class WeeklyReportScreen extends StatefulWidget {
  const WeeklyReportScreen({
    super.key,
    required this.client,
    required this.sound,
    required this.saved,
  });

  final RedunatorApiClient client;
  final SoundService sound;
  final SavedService saved;

  @override
  State<WeeklyReportScreen> createState() => _WeeklyReportScreenState();
}

class _WeeklyReportScreenState extends State<WeeklyReportScreen> {
  List<WeeklyCameraSummary> _summaries = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    final saved = widget.saved.saved;
    if (saved.isEmpty) {
      if (!mounted) return;
      setState(() {
        _summaries = [];
        _loading = false;
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    final out = <WeeklyCameraSummary>[];
    await Future.wait(
      saved.map((s) async {
        try {
          final trends = await widget.client.getViolationTrends(
            cameraId: s.cameraId,
          );
          final summary = weeklySummary(name: s.intersection, days: trends.days);
          if (summary != null) out.add(summary);
        } catch (_) {
          // One camera failing must not kill the whole report.
        }
      }),
    );
    if (!mounted) return;
    setState(() {
      _summaries = out
        ..sort((a, b) => b.thisWeekMean.compareTo(a.thisWeekMean));
      _loading = false;
      if (out.isEmpty) _error = 'No trend data yet for your saved cameras.';
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Weekly risk report')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  const Text(
                    'Your saved intersections, one week in review. '
                    'This week is the most recent week of city data '
                    '(the feed lags about two weeks behind today). '
                    'All values are observed daily violations.',
                  ),
                  const SizedBox(height: 16),
                  if (_error != null)
                    Text(
                      _error!,
                      style: const TextStyle(color: Colors.red),
                    ),
                  if (_summaries.isEmpty && _error == null)
                    const Padding(
                      padding: EdgeInsets.only(top: 24),
                      child: Text(
                        'No saved intersections yet. Save cameras on '
                        'your route and they will show up here.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ..._summaries.map(
                    (s) => Card(
                      child: ListTile(
                        leading: Icon(
                          s.percentChange != null && s.percentChange! > 5
                              ? Icons.trending_up
                              : s.percentChange != null &&
                                      s.percentChange! < -5
                                  ? Icons.trending_down
                                  : Icons.trending_flat,
                          color:
                              s.percentChange != null && s.percentChange! > 5
                              ? Colors.red
                              : s.percentChange != null &&
                                      s.percentChange! < -5
                                  ? Colors.green
                                  : Colors.grey,
                        ),
                        title: Text(s.name),
                        subtitle: Text(
                          'This week: ${s.thisWeekMean.toStringAsFixed(1)}/day'
                          '${s.priorWeekMean == null ? '' : ' · Prior week: ${s.priorWeekMean!.toStringAsFixed(1)}/day'}'
                          '${s.percentChange == null ? '' : s.percentChange! >= 0 ? ' · +${s.percentChange!.toStringAsFixed(0)}%' : ' · ${s.percentChange!.toStringAsFixed(0)}%'}'
                          '${s.worstDay == null ? '' : '\nWorst day: ${s.worstDay} (${s.worstValue!.toStringAsFixed(1)}/day)'}'
                          '${s.spikeDays > 0 ? ' · ${s.spikeDays} spike day${s.spikeDays == 1 ? '' : 's'}' : ''}',
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
    );
  }
}
