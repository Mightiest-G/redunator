// Intersection query screen: pick an intersection (from the
// /intersections list), pick a date + hour, and see the risk for that
// hour — labelled honestly as the hourly ESTIMATE for that hour.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import '../api/client.dart';
import '../api/models.dart';
import '../services/risk.dart';
import '../services/sound_service.dart';
import '../widgets/hourly_chart.dart';

class QueryScreen extends StatefulWidget {
  const QueryScreen({super.key, required this.client, required this.sound});
  final RedunatorApiClient client;
  final SoundService sound;

  @override
  State<QueryScreen> createState() => _QueryScreenState();
}

class _QueryScreenState extends State<QueryScreen> {
  List<Intersection> _all = [];
  List<Intersection> _filtered = [];
  Intersection? _selected;
  DateTime _date = DateTime.now();
  int _hour = DateTime.now().hour;
  Prediction? _prediction;
  bool _loadingList = true;
  bool _loadingPrediction = false;
  String? _error;
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _loadList();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadList() async {
    try {
      final list = await widget.client.getIntersections(limit: 300);
      if (!mounted) return;
      setState(() {
        _all = list;
        _filtered = list;
        _loadingList = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingList = false;
        _error = 'Could not load intersections: ${e.message}';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingList = false;
        _error = 'Could not reach the backend: $e';
      });
    }
  }

  void _onSearch(String q) {
    final needle = q.trim().toUpperCase();
    setState(() {
      _filtered = needle.isEmpty
          ? _all
          : _all.where((c) => c.intersection.contains(needle)).toList();
    });
  }

  Future<void> _query() async {
    final cam = _selected;
    if (cam == null) return;
    setState(() {
      _loadingPrediction = true;
      _error = null;
    });
    try {
      final ts = DateTime(_date.year, _date.month, _date.day, _hour);
      final pred = await widget.client.predictRisk(
        cameraId: cam.cameraId,
        timestamp: ts,
      );
      if (!mounted) return;
      setState(() {
        _prediction = pred;
        _loadingPrediction = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingPrediction = false;
        _error = 'Prediction failed: ${e.message}';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingPrediction = false;
        _error = 'Could not reach the backend: $e';
      });
    }
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(2023, 1, 1),
      lastDate: DateTime.now().add(const Duration(days: 365)),
    );
    if (picked != null) setState(() => _date = picked);
  }

  @override
  Widget build(BuildContext context) {
    final pred = _prediction;
    final hourEntry = pred?.hourEntry(_hour);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        TextField(
          controller: _searchController,
          decoration: const InputDecoration(
            labelText: 'Search intersection',
            prefixIcon: Icon(Icons.search),
            border: OutlineInputBorder(),
          ),
          onChanged: _onSearch,
        ),
        const SizedBox(height: 12),
        if (_loadingList)
          const Center(child: CircularProgressIndicator())
        else if (_error != null && _all.isEmpty)
          Text(_error!, style: const TextStyle(color: Colors.red))
        else
          Container(
            decoration: BoxDecoration(
              border: Border.all(color: Colors.grey.shade400),
              borderRadius: BorderRadius.circular(4),
            ),
            constraints: const BoxConstraints(maxHeight: 220),
            child: ListView.builder(
              shrinkWrap: true,
              itemCount: _filtered.length,
              itemBuilder: (context, i) {
                final cam = _filtered[i];
                final selected = cam.cameraId == _selected?.cameraId;
                return ListTile(
                  dense: true,
                  selected: selected,
                  title: Text(cam.intersection),
                  subtitle: Text(
                    'Camera ${cam.cameraId} · typical weekday '
                    '${cam.typicalWeekdayPred.toStringAsFixed(1)}/day '
                    '(${cam.typicalWeekdayBand})',
                  ),
                  trailing: selected
                      ? const Icon(Icons.check, color: Colors.green)
                      : null,
                  onTap: () {
                    unawaited(widget.sound.playQuery());
                    setState(() {
                      _selected = cam;
                      _prediction = null;
                    });
                    _query();
                  },
                );
              },
            ),
          ),
        const SizedBox(height: 16),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () {
                  unawaited(widget.sound.playTap());
                  _pickDate();
                },
                icon: const Icon(Icons.calendar_today),
                label: Text(
                  '${_date.year}-${_date.month.toString().padLeft(2, '0')}-${_date.day.toString().padLeft(2, '0')}',
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: DropdownButtonFormField<int>(
                initialValue: _hour,
                decoration: const InputDecoration(
                  labelText: 'Hour',
                  border: OutlineInputBorder(),
                ),
                items: List.generate(
                  24,
                  (h) => DropdownMenuItem(
                    value: h,
                    child: Text('${h.toString().padLeft(2, '0')}:00'),
                  ),
                ),
                onChanged: (h) {
                  if (h == null) return;
                  setState(() => _hour = h);
                  if (_selected != null) _query();
                },
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        if (_loadingPrediction)
          const Center(child: CircularProgressIndicator())
        else if (_error != null)
          Text(_error!, style: const TextStyle(color: Colors.red))
        else if (pred != null && hourEntry != null)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    pred.intersection ?? _selected?.intersection ?? '',
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Risk at ${_hour.toString().padLeft(2, '0')}:00 on '
                    '${pred.date}: ${formatPercent(hourEntry.riskScore)} '
                    '(${hourEntry.band}) — estimate: '
                    '${hourEntry.predictedViolations.toStringAsFixed(2)} '
                    'violations/hour.',
                    style: const TextStyle(fontSize: 16),
                  ),
                  const SizedBox(height: 4),
                  const Text(hourlyEstimateLabel),
                  const SizedBox(height: 8),
                  Text(
                    'Daily total (modelled): '
                    '${pred.dailyPrediction.toStringAsFixed(1)} '
                    'violations (${pred.riskLevel}, source: '
                    '${pred.dailySource}).',
                  ),
                  const SizedBox(height: 12),
                  HourlyChart(
                    hourly: pred.hourly,
                    selectedHour: _hour,
                    onHourSelected: (h) => setState(() => _hour = h),
                  ),
                  if (pred.reasons.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    const Text(
                      'Why this risk',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    ...pred.reasons.map((r) => Text('• $r')),
                  ],
                ],
              ),
            ),
          )
        else
          const Text(
            'Pick an intersection above to see its risk for the chosen hour.',
          ),
      ],
    );
  }
}
