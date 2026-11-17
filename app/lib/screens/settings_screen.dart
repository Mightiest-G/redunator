// Settings screen: alert distance (default 300 m), risk threshold
// (default 75%), backend base URL, Google Maps API key status, units,
// plus backend health, alert-sound test, feedback, and the honest
// background-location note. Persisted locally with shared_preferences.
import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import '../api/client.dart';
import '../api/models.dart';
import '../config.dart';
import '../services/cache_service.dart';
import '../services/proximity_service.dart';
import '../services/settings_service.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.settings,
    required this.client,
    required this.proximity,
    required this.onSettingsChanged,
  });
  final AppSettings settings;
  final RedunatorApiClient client;
  final ProximityService proximity;
  final VoidCallback onSettingsChanged;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _baseUrlController = TextEditingController();
  final _userIdController = TextEditingController();
  final _feedbackController = TextEditingController();
  int _feedbackRating = 5;
  String? _healthText;
  String? _feedbackResult;
  bool _checkingHealth = false;
  bool _sendingFeedback = false;
  bool _syncingSettings = false;

  @override
  void initState() {
    super.initState();
    _baseUrlController.text = widget.settings.baseUrl;
    _userIdController.text = widget.settings.userId;
  }

  @override
  void dispose() {
    _baseUrlController.dispose();
    _userIdController.dispose();
    _feedbackController.dispose();
    super.dispose();
  }

  Future<void> _checkHealth() async {
    setState(() {
      _checkingHealth = true;
      _healthText = null;
    });
    try {
      final h = await widget.client.getHealth();
      if (!mounted) return;
      setState(() {
        _healthText =
            'OK — model v${h.modelVersion}, latest violation data ${h.latestViolationDate}.';
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _healthText = 'Backend error: ${e.message}');
    } catch (e) {
      if (!mounted) return;
      setState(() => _healthText = 'Unreachable: $e');
    } finally {
      if (mounted) setState(() => _checkingHealth = false);
    }
  }

  Future<void> _sendFeedback() async {
    final text = _feedbackController.text.trim();
    if (text.isEmpty) return;
    setState(() {
      _sendingFeedback = true;
      _feedbackResult = null;
    });
    try {
      await widget.client.sendFeedback(
        UserFeedback(text: text, rating: _feedbackRating, category: 'app'),
      );
      if (!mounted) return;
      setState(() {
        _feedbackResult = 'Feedback sent — thank you.';
        _feedbackController.clear();
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _feedbackResult = 'Could not send: ${e.message}');
    } finally {
      if (mounted) setState(() => _sendingFeedback = false);
    }
  }

  Future<void> _syncToBackend() async {
    setState(() => _syncingSettings = true);
    try {
      await widget.client.updateUserSettings({
        'units': widget.settings.units,
        'alert_radius_m': widget.settings.alertDistanceM,
        'risk_threshold': widget.settings.riskThresholdPct >= 75
            ? 'HIGH'
            : widget.settings.riskThresholdPct >= 40
                ? 'MEDIUM'
                : 'LOW',
      });
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Settings saved to the backend.')),
      );
    } on ApiException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not save to backend: ${e.message}')),
      );
    } finally {
      if (mounted) setState(() => _syncingSettings = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.settings;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Proximity alerts',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        Text(
          'Alert distance: ${s.formatDistance(s.alertDistanceM)}',
        ),
        Slider(
          value: s.alertDistanceM,
          min: 50,
          max: 2000,
          divisions: 39,
          label: '${s.alertDistanceM.round()} m',
          onChanged: (v) async {
            await s.setAlertDistanceM(v);
            setState(() {});
            widget.onSettingsChanged();
          },
        ),
        Text(
          'Risk threshold: ${s.riskThresholdPct.round()}% '
          '(alert when a camera within range is above this risk)',
        ),
        Slider(
          value: s.riskThresholdPct,
          min: 10,
          max: 100,
          divisions: 18,
          label: '${s.riskThresholdPct.round()}%',
          onChanged: (v) async {
            await s.setRiskThresholdPct(v);
            setState(() {});
            widget.onSettingsChanged();
          },
        ),
        const SizedBox(height: 8),
        const Text(
          'Alerts work while the app is open (foreground). Background '
          'alerts are NOT supported in this version: Android/iOS only '
          'allow background location with an "always" permission plus a '
          'foreground service / background mode, which this build does '
          'not run. If you leave the app, alerts pause until you reopen it.',
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: () async {
            await widget.proximity.testSound();
            if (!context.mounted) return;
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Alert sound played.')),
            );
          },
          icon: const Icon(Icons.volume_up),
          label: const Text('Test alert sound'),
        ),
        const Divider(height: 32),
        const Text(
          'Backend',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _baseUrlController,
          decoration: const InputDecoration(
            labelText: 'Backend base URL',
            hintText: 'http://10.0.2.2:8000',
            border: OutlineInputBorder(),
          ),
          onSubmitted: (v) async {
            await s.setBaseUrl(v);
            widget.onSettingsChanged();
            setState(() {});
          },
        ),
        const SizedBox(height: 4),
        const Text(
          'Default http://10.0.2.2:8000 reaches a backend on the host '
          'machine from the Android emulator. For a physical device use '
          'the host LAN IP or a deployed URL.',
          style: TextStyle(fontSize: 12, color: Colors.grey),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _userIdController,
          decoration: const InputDecoration(
            labelText: 'User ID (for reports / alerts)',
            border: OutlineInputBorder(),
          ),
          onSubmitted: (v) async {
            await s.setUserId(v);
            widget.onSettingsChanged();
            setState(() {});
          },
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            ElevatedButton(
              onPressed: _checkingHealth ? null : _checkHealth,
              child: Text(_checkingHealth ? 'Checking…' : 'Check backend health'),
            ),
            const SizedBox(width: 8),
            ElevatedButton(
              onPressed: _syncingSettings ? null : _syncToBackend,
              child: Text(_syncingSettings ? 'Saving…' : 'Save settings to backend'),
            ),
          ],
        ),
        if (_healthText != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_healthText!),
          ),
        const Divider(height: 32),
        const Text(
          'Map',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: Icon(
            hasMapsApiKey ? Icons.check_circle : Icons.error,
            color: hasMapsApiKey ? Colors.green : Colors.red,
          ),
          title: Text(
            hasMapsApiKey
                ? 'Google Maps API key configured'
                : 'Google Maps API key MISSING',
          ),
          subtitle: Text(
            hasMapsApiKey
                ? 'The map is enabled.'
                : 'Add the key in lib/config.dart and rebuild — see the '
                    'Map tab for step-by-step instructions.',
          ),
        ),
        const Divider(height: 32),
        const Text(
          'Units',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'metric', label: Text('Metric')),
            ButtonSegment(value: 'imperial', label: Text('Imperial')),
          ],
          selected: {s.units},
          onSelectionChanged: (sel) async {
            await s.setUnits(sel.first);
            setState(() {});
          },
        ),
        const Divider(height: 32),
        const Text(
          'Offline cache',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: () async {
            await CacheService().clear();
            if (!context.mounted) return;
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Cached data cleared.')),
            );
          },
          child: const Text('Clear cached intersections / predictions'),
        ),
        const Divider(height: 32),
        const Text(
          'Feedback',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _feedbackController,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: 'Tell us what worked or did not',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            const Text('Rating:'),
            const SizedBox(width: 8),
            DropdownButton<int>(
              value: _feedbackRating,
              items: List.generate(
                5,
                (i) => DropdownMenuItem(
                  value: i + 1,
                  child: Text('${i + 1}'),
                ),
              ),
              onChanged: (v) {
                if (v != null) setState(() => _feedbackRating = v);
              },
            ),
            const Spacer(),
            ElevatedButton(
              onPressed: _sendingFeedback ? null : _sendFeedback,
              child: Text(_sendingFeedback ? 'Sending…' : 'Send feedback'),
            ),
          ],
        ),
        if (_feedbackResult != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_feedbackResult!),
          ),
        const SizedBox(height: 24),
        const Text(
          'Redunator 1.0.0 — hourly values are estimates from proxies, '
          'not observed hourly violations.',
          style: TextStyle(fontSize: 12, color: Colors.grey),
        ),
      ],
    );
  }
}
