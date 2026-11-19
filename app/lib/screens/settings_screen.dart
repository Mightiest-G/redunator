// Settings screen: alert distance (default 300 m), risk threshold
// (default 75%), backend base URL, Google Maps API key status, units,
// plus backend health, alert-sound test, feedback, Pro background
// alerts + morning digest, and the honest background-location note.
// Persisted locally with shared_preferences.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:geolocator/geolocator.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../config.dart';
import '../services/background_alerts.dart';
import '../services/cache_service.dart';
import '../services/morning_digest.dart';
import '../services/proximity_service.dart';
import '../services/saved_service.dart';
import '../services/settings_service.dart';
import '../services/sound_service.dart';
import '../services/subscription_service.dart';
import '../services/update_service.dart';
import '../widgets/pro_upsell.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.settings,
    required this.client,
    required this.proximity,
    required this.sound,
    required this.subscription,
    required this.backgroundAlerts,
    required this.digest,
    required this.saved,
    required this.onSettingsChanged,
  });
  final AppSettings settings;
  final RedunatorApiClient client;
  final ProximityService proximity;
  final SoundService sound;
  final SubscriptionService subscription;
  final BackgroundAlertsService backgroundAlerts;
  final MorningDigestService digest;
  final SavedService saved;
  final VoidCallback onSettingsChanged;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _baseUrlController = TextEditingController();
  final _feedbackController = TextEditingController();
  int _feedbackRating = 5;
  String? _healthText;
  String? _feedbackResult;
  bool _checkingHealth = false;
  bool _sendingFeedback = false;
  bool _syncingSettings = false;
  String _versionLabel = '…';
  bool _checkingUpdate = false;
  String? _updateText;
  String? _bgStatusText;
  bool _bgNeedsSettings = false;
  bool _requestingBgPermissions = false;
  bool _sendingTestDigest = false;
  String? _testDigestResult;

  @override
  void initState() {
    super.initState();
    _baseUrlController.text = widget.settings.baseUrl;
    _loadVersionLabel();
    _refreshBackgroundStatus();
  }

  /// Permission state for the background-alerts section. Failures
  /// just leave the status text empty (the section still works).
  Future<void> _refreshBackgroundStatus() async {
    try {
      final st = await widget.backgroundAlerts.permissionStatus();
      if (!mounted) return;
      setState(() {
        _bgStatusText = st.ready
            ? 'Ready: notifications allowed, location "Allow all the time".'
            : 'Notifications: ${st.notificationsGranted ? 'allowed' : 'not allowed'} • '
                  'Location: ${_locationLabel(st.location)}.';
        _bgNeedsSettings =
            st.location == LocationPermission.whileInUse ||
            st.location == LocationPermission.denied;
      });
    } catch (_) {
      // Status is informational only.
    }
  }

  static String _locationLabel(LocationPermission p) {
    switch (p) {
      case LocationPermission.always:
        return 'Allow all the time';
      case LocationPermission.whileInUse:
        return 'While using the app';
      case LocationPermission.deniedForever:
        return 'Denied permanently';
      case LocationPermission.denied:
        return 'Denied';
      case LocationPermission.unableToDetermine:
        return 'Unknown';
    }
  }

  Future<void> _requestBackgroundPermissions() async {
    setState(() => _requestingBgPermissions = true);
    try {
      await widget.backgroundAlerts.requestPermissions();
      // Exact alarms make the 07:00 digest land on time; a denial
      // just falls back to inexact scheduling.
      await widget.backgroundAlerts.requestExactAlarmsPermission();
    } catch (_) {
      // Permission flow failures are reported via the status text.
    } finally {
      if (mounted) setState(() => _requestingBgPermissions = false);
    }
    await _refreshBackgroundStatus();
  }

  Future<void> _sendTestDigest() async {
    setState(() {
      _sendingTestDigest = true;
      _testDigestResult = null;
    });
    final ok = await widget.digest.sendTestDigest(client: widget.client);
    if (!mounted) return;
    setState(() {
      _sendingTestDigest = false;
      _testDigestResult = ok
          ? 'Test digest sent — check your notifications.'
          : 'Could not build the digest (are you online?).';
    });
  }

  Future<void> _loadVersionLabel() async {
    final label = await UpdateService.currentVersionLabel();
    if (!mounted) return;
    setState(() => _versionLabel = label);
  }

  Future<void> _checkForUpdate() async {
    setState(() {
      _checkingUpdate = true;
      _updateText = null;
    });
    final info = await UpdateService.checkForUpdate(widget.client);
    if (!mounted) return;
    setState(() => _checkingUpdate = false);
    if (info == null) {
      setState(() => _updateText = 'You are up to date ($_versionLabel).');
      return;
    }
    final opened = await UpdateService.showUpdateDialog(context, info);
    if (!mounted) return;
    setState(() {
      _updateText = opened
          ? 'Downloading ${info.tag}…'
          : 'Update ${info.tag} is available — tap Check for updates '
                'again to download it.';
    });
  }

  @override
  void dispose() {
    _baseUrlController.dispose();
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
        Text('Alert distance: ${s.formatDistance(s.alertDistanceM)}'),
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
          'Alerts work while the app is open. Pro users can also '
          'enable background alerts below, which keep watching while '
          'the app is closed.',
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: () async {
            unawaited(widget.sound.playTap());
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
          'Pro alert distance',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        AnimatedBuilder(
          animation: widget.subscription,
          builder: (context, _) {
            if (!widget.subscription.isPro) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Choose how far out proximity alerts warn you — '
                    '100 m, 250 m, 500 m or 1 km. A Pro feature; free '
                    'users keep the slider above.',
                  ),
                  ProUpsellCard(
                    subscription: widget.subscription,
                    sound: widget.sound,
                    showTitle: false,
                  ),
                ],
              );
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'How far out proximity alerts warn you. Applies '
                  'immediately — the watcher restarts with the new '
                  'distance.',
                ),
                const SizedBox(height: 8),
                SegmentedButton<double>(
                  segments: AppSettings.proAlertDistanceOptions
                      .map(
                        (d) => ButtonSegment<double>(
                          value: d,
                          label: Text(s.formatDistance(d)),
                        ),
                      )
                      .toList(),
                  selected: {s.proAlertDistanceM},
                  onSelectionChanged: (sel) async {
                    unawaited(widget.sound.playTap());
                    await s.setProAlertDistanceM(sel.first);
                    setState(() {});
                    widget.onSettingsChanged();
                  },
                ),
              ],
            );
          },
        ),
        const Divider(height: 32),
        const Text(
          'Background alerts',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        AnimatedBuilder(
          animation: widget.subscription,
          builder: (context, _) {
            if (!widget.subscription.isPro) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Pro keeps watching even when the app is closed: '
                    'about every 15 minutes it checks whether you are '
                    'near a HIGH-risk camera and sends a local '
                    'notification — at most one per camera per day. '
                    'Needs the notification permission and location '
                    'set to "Allow all the time".',
                  ),
                  ProUpsellCard(
                    subscription: widget.subscription,
                    sound: widget.sound,
                    showTitle: false,
                  ),
                ],
              );
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Background alerts'),
                  subtitle: Text(
                    _bgStatusText ?? 'Checking permissions…',
                  ),
                  value: s.backgroundAlertsEnabled,
                  onChanged: (v) async {
                    unawaited(widget.sound.playTap());
                    await s.setBackgroundAlertsEnabled(v);
                    try {
                      await widget.backgroundAlerts.syncWithPro(
                        isPro: widget.subscription.isPro,
                        enabled: v,
                      );
                    } catch (_) {
                      // A scheduling failure leaves the toggle saved;
                      // the next app start retries the registration.
                    }
                    setState(() {});
                  },
                ),
                const Text(
                  'Checks about every 15 minutes while Pro is active '
                  '(Android may defer a run in Doze / battery saver, '
                  'so a background alert can arrive late). Only the '
                  'same risk lookups the app already makes leave the '
                  'phone — no location is sent anywhere else.',
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: _requestingBgPermissions
                      ? null
                      : () {
                          unawaited(widget.sound.playTap());
                          _requestBackgroundPermissions();
                        },
                  icon: const Icon(Icons.location_on),
                  label: Text(
                    _requestingBgPermissions
                        ? 'Requesting…'
                        : 'Enable notifications & location',
                  ),
                ),
                if (_bgNeedsSettings) ...[
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: () async {
                      unawaited(widget.sound.playTap());
                      await widget.backgroundAlerts.openAppSettings();
                      await _refreshBackgroundStatus();
                    },
                    icon: const Icon(Icons.settings),
                    label: const Text(
                      'Open app settings (allow location all the time)',
                    ),
                  ),
                ],
              ],
            );
          },
        ),
        const Divider(height: 32),
        const Text(
          'Morning digest',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        AnimatedBuilder(
          animation: widget.subscription,
          builder: (context, _) {
            if (!widget.subscription.isPro) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Pro sends a daily 07:00 summary of today’s '
                    'predicted risk for your saved intersections, '
                    'plus the safest time to leave (estimates). '
                    'With nothing saved yet, the digest reminds you '
                    'to save some.',
                  ),
                  ProUpsellCard(
                    subscription: widget.subscription,
                    sound: widget.sound,
                    showTitle: false,
                  ),
                ],
              );
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Morning digest'),
                  subtitle: const Text(
                    'Daily at 07:00: today’s predicted risk for your '
                    'saved intersections, plus the safest time to '
                    'leave (estimates).',
                  ),
                  value: s.morningDigestEnabled,
                  onChanged: (v) async {
                    unawaited(widget.sound.playTap());
                    await s.setMorningDigestEnabled(v);
                    try {
                      await widget.digest.ensureScheduled(
                        client: widget.client,
                        isPro: widget.subscription.isPro,
                        enabled: v,
                        force: true,
                      );
                    } catch (_) {
                      // A scheduling failure leaves the toggle saved;
                      // the next app start retries.
                    }
                    setState(() {});
                  },
                ),
                const Text(
                  'Covers your saved intersections. With none saved, '
                  'the digest reminds you to save some instead of '
                  'sending an empty summary.',
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: _sendingTestDigest
                      ? null
                      : () {
                          unawaited(widget.sound.playTap());
                          _sendTestDigest();
                        },
                  icon: const Icon(Icons.notifications),
                  label: Text(
                    _sendingTestDigest
                        ? 'Sending…'
                        : 'Send test digest now',
                  ),
                ),
                if (_testDigestResult != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(_testDigestResult!),
                  ),
              ],
            );
          },
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
            hintText: 'https://redunator-api-108691448805.us-central1.run.app',
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
          'Default is the live Cloud Run backend. For local '
          'development use http://10.0.2.2:8000 (emulator) or the host '
          'LAN IP (physical device).',
          style: TextStyle(fontSize: 12, color: Colors.grey),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            ElevatedButton(
              onPressed: _checkingHealth
                  ? null
                  : () {
                      unawaited(widget.sound.playTap());
                      _checkHealth();
                    },
              child: Text(
                _checkingHealth ? 'Checking…' : 'Check backend health',
              ),
            ),
            const SizedBox(width: 8),
            ElevatedButton(
              onPressed: _syncingSettings
                  ? null
                  : () {
                      unawaited(widget.sound.playTap());
                      _syncToBackend();
                    },
              child: Text(
                _syncingSettings ? 'Saving…' : 'Save settings to backend',
              ),
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
            unawaited(widget.sound.playTap());
            await s.setUnits(sel.first);
            setState(() {});
          },
        ),
        const Divider(height: 32),
        const Text(
          'Appearance',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'system', label: Text('System')),
            ButtonSegment(value: 'light', label: Text('Light')),
            ButtonSegment(value: 'dark', label: Text('Dark')),
          ],
          selected: {s.themeMode},
          onSelectionChanged: (sel) async {
            unawaited(widget.sound.playTap());
            await s.setThemeMode(sel.first);
            setState(() {});
          },
        ),
        const SizedBox(height: 4),
        const Text(
          'System follows your phone. Dark also darkens the map.',
          style: TextStyle(fontSize: 12, color: Colors.grey),
        ),
        const Divider(height: 32),
        const Text(
          'Sound',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Tap sounds'),
          subtitle: const Text(
            'A short click when you tap buttons, tabs and map markers. '
            'The proximity alert sound is separate and is not affected.',
          ),
          value: s.soundEnabled,
          onChanged: (v) async {
            await s.setSoundEnabled(v);
            setState(() {});
            // Plays only when turning on (the service is disabled
            // the moment the toggle goes off).
            unawaited(widget.sound.playTap());
          },
        ),
        OutlinedButton.icon(
          onPressed: () async {
            unawaited(widget.sound.testTap());
            if (!context.mounted) return;
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(const SnackBar(content: Text('Tap sound played.')));
          },
          icon: const Icon(Icons.volume_up),
          label: const Text('Test tap sound'),
        ),
        const Divider(height: 32),
        const Text(
          'Redunator Pro',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        AnimatedBuilder(
          animation: widget.subscription,
          builder: (context, _) {
            if (widget.subscription.isPro) {
              return const ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.workspace_premium, color: Colors.amber),
                title: Text('Pro is active'),
                subtitle: Text(
                  'Route planning, proximity alerts, saved intersections '
                  'with spike alerts, risk trends, safest time to leave, '
                  'a custom alert distance, background alerts and the '
                  'morning digest are unlocked.',
                ),
              );
            }
            return ProUpsellCard(
              subscription: widget.subscription,
              sound: widget.sound,
              showTitle: false,
            );
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
            unawaited(widget.sound.playTap());
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
          'App updates',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        Text('Current version: $_versionLabel'),
        const SizedBox(height: 8),
        ElevatedButton(
          onPressed: _checkingUpdate
              ? null
              : () {
                  unawaited(widget.sound.playTap());
                  _checkForUpdate();
                },
          child: Text(_checkingUpdate ? 'Checking…' : 'Check for updates'),
        ),
        if (_updateText != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_updateText!),
          ),
        const SizedBox(height: 4),
        const Text(
          'The app also checks once when it opens. New versions come '
          'from the same place you installed this one.',
          style: TextStyle(fontSize: 12, color: Colors.grey),
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
                (i) => DropdownMenuItem(value: i + 1, child: Text('${i + 1}')),
              ),
              onChanged: (v) {
                if (v != null) setState(() => _feedbackRating = v);
              },
            ),
            const Spacer(),
            ElevatedButton(
              onPressed: _sendingFeedback
                  ? null
                  : () {
                      unawaited(widget.sound.playTap());
                      _sendFeedback();
                    },
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
        Text(
          'Redunator $_versionLabel — hourly values are estimates from '
          'proxies, not observed hourly violations.',
          style: const TextStyle(fontSize: 12, color: Colors.grey),
        ),
      ],
    );
  }
}
