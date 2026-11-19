// Redunator — Chicago red-light-camera ticket-risk predictor.
// Tabs: Map (home), Query, Saved (Pro), Route (Pro), Settings.
// The backend client is rebuilt whenever the base URL / user ID
// changes in Settings. No mock data anywhere in the shipped app:
// every number comes from the backend or from the on-device cache
// (labelled as cached with its age).
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import 'api/client.dart';
import 'config.dart';
import 'screens/map_screen.dart';
import 'screens/query_screen.dart';
import 'screens/route_screen.dart';
import 'screens/saved_screen.dart';
import 'screens/settings_screen.dart';
import 'services/auth_service.dart';
import 'services/background_alerts.dart';
import 'services/local_notifications.dart';
import 'services/morning_digest.dart';
import 'services/proximity_service.dart';
import 'services/saved_service.dart';
import 'services/settings_service.dart';
import 'services/sound_service.dart';
import 'services/subscription_service.dart';
import 'services/update_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Settings are loaded before the app starts so the theme (dark mode)
  // is right on the very first frame — no light flash on a dark phone.
  final settings = await AppSettings.load();
  // Local notifications (Pro background alerts + morning digest):
  // timezone first (the 07:00 digest is device-local), then the
  // plugin, then WorkManager's background entry point. None of this
  // may block startup: if background scheduling fails here, the
  // foreground app still works and Settings shows the state.
  try {
    await LocalNotifications.initTimeZone();
    await LocalNotifications().init();
  } catch (_) {
    // Notifications are a Pro extra; the app works without them.
  }
  try {
    await BackgroundAlertsService.initializeWorkmanager();
  } catch (_) {
    // Background alerts simply stay unregistered until a later sync.
  }
  runApp(RedunatorApp(settings: settings));
}

class RedunatorApp extends StatelessWidget {
  const RedunatorApp({super.key, required this.settings});
  final AppSettings settings;

  @override
  Widget build(BuildContext context) {
    // Rebuilds the whole app (including the map style) whenever a
    // setting changes — the theme setting lives in AppSettings.
    return AnimatedBuilder(
      animation: settings,
      builder: (context, _) => MaterialApp(
        title: 'Redunator',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.red),
          useMaterial3: true,
        ),
        darkTheme: ThemeData(
          colorScheme: ColorScheme.fromSeed(
            seedColor: Colors.red,
            brightness: Brightness.dark,
          ),
          useMaterial3: true,
        ),
        themeMode: settings.themeModeEnum,
        home: _HomePage(settings: settings),
      ),
    );
  }
}

class _HomePage extends StatefulWidget {
  const _HomePage({required this.settings});
  final AppSettings settings;

  @override
  State<_HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<_HomePage> {
  RedunatorApiClient? _client;
  final ProximityService _proximity = ProximityService();
  final SoundService _sound = SoundService();
  final TokenService _auth = TokenService();
  final LocalNotifications _notifications = LocalNotifications();
  late final BackgroundAlertsService _bgAlerts;
  late final MorningDigestService _digest;
  SubscriptionService? _subscription;
  SavedService? _saved;
  int _tab = 0;
  bool _updateChecked = false;

  @override
  void initState() {
    super.initState();
    _client = _buildClient(widget.settings);
    _wireAuth(_client!);
    _bgAlerts = BackgroundAlertsService(notifications: _notifications);
    _digest = MorningDigestService(notifications: _notifications);
    // Subscriptions: load cached Pro state, subscribe to the purchase
    // stream and query the store for the Pro products.
    _subscription = SubscriptionService(client: _client);
    unawaited(_subscription!.init());
    // Pro background features follow isPro: when Pro flips on, the
    // 15-minute background task registers and the 07:00 digest is
    // scheduled; when it flips off, both are cancelled.
    _subscription!.addListener(_syncProFeatures);
    // Saved intersections (Pro): local list + server mirror.
    _saved = SavedService(client: _client);
    unawaited(_saved!.load());
    // A save/unsave changes what the digest covers, so re-schedule
    // it with fresh content.
    _saved!.addListener(_onSavedChanged);
    // Device auth: load/create the device UUID, point the
    // subscription service at it, and mint the JWT in the background
    // (public endpoints work without it; user endpoints wait for it).
    unawaited(_initAuth());
    // One update check per launch, after the first frame (so the
    // dialog has a Navigator to show in).
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkForUpdate());
    // Pro background features need the client + the loaded Pro state,
    // so sync once after the first frame too.
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => unawaited(_syncProFeatures()),
    );
  }

  /// (Un)registers the background alert task and (un)schedules the
  /// morning digest for the current Pro state. Scheduling must never
  /// break the UI: failures are swallowed (Settings shows the
  /// permission state, and the next sync retries).
  Future<void> _syncProFeatures() async {
    final sub = _subscription;
    final client = _client;
    if (sub == null || client == null) return;
    try {
      await _bgAlerts.syncWithPro(
        isPro: sub.isPro,
        enabled: widget.settings.backgroundAlertsEnabled,
      );
    } catch (_) {}
    try {
      await _digest.ensureScheduled(
        client: client,
        isPro: sub.isPro,
        enabled: widget.settings.morningDigestEnabled,
      );
    } catch (_) {}
  }

  void _onSavedChanged() {
    final client = _client;
    final sub = _subscription;
    if (client == null || sub == null) return;
    unawaited(
      _digest.ensureScheduled(
        client: client,
        isPro: sub.isPro,
        enabled: widget.settings.morningDigestEnabled,
        force: true,
      ),
    );
  }

  /// Points a client at the token refresh: on a 401 the client asks
  /// here for a fresh token, then retries the request once.
  void _wireAuth(RedunatorApiClient c) {
    c.onUnauthorized = () async {
      await _auth.refreshToken(c);
    };
  }

  Future<void> _initAuth() async {
    final deviceId = await _auth.load();
    _subscription?.deviceId = deviceId;
    final client = _client;
    if (client != null) await _auth.ensureToken(client);
  }

  Future<void> _checkForUpdate() async {
    if (_updateChecked) return;
    _updateChecked = true;
    final client = _client;
    if (client == null || !mounted) return;
    final info = await UpdateService.checkForUpdate(client);
    if (info == null || !mounted) return;
    await UpdateService.showUpdateDialog(context, info);
  }

  RedunatorApiClient _buildClient(AppSettings s) =>
      RedunatorApiClient(baseUrl: s.baseUrl);

  void _onSettingsChanged() {
    setState(() {
      _client = _buildClient(widget.settings);
      _wireAuth(_client!);
      _subscription?.client = _client;
      _saved?.client = _client;
      // The new client has no token yet; the cached one (if any) is
      // re-attached from storage.
      unawaited(_auth.ensureToken(_client!));
      // The digest was scheduled against the old client; re-sync it
      // (and the background task) with the new one.
      unawaited(_syncProFeatures());
    });
  }

  @override
  void dispose() {
    _subscription?.removeListener(_syncProFeatures);
    _saved?.removeListener(_onSavedChanged);
    _proximity.dispose();
    _sound.dispose();
    _subscription?.dispose();
    _saved?.dispose();
    _client?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.settings;
    // The tap-sound toggle lives in Settings; keep the shared service
    // in sync whenever settings change (AnimatedBuilder rebuilds us).
    _sound.enabled = settings.soundEnabled;
    final client = _client;
    if (client == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    final screens = <Widget>[
      MapScreen(
        key: ValueKey('map:${client.baseUrl}'),
        settings: settings,
        client: client,
        proximity: _proximity,
        sound: _sound,
        subscription: _subscription!,
        saved: _saved!,
        onBaseUrlChanged: _onSettingsChanged,
      ),
      QueryScreen(client: client, sound: _sound),
      SavedScreen(
        client: client,
        settings: settings,
        sound: _sound,
        subscription: _subscription!,
        saved: _saved!,
      ),
      RouteScreen(
        client: client,
        proximity: _proximity,
        sound: _sound,
        subscription: _subscription!,
      ),
      SettingsScreen(
        settings: settings,
        client: client,
        proximity: _proximity,
        sound: _sound,
        subscription: _subscription!,
        backgroundAlerts: _bgAlerts,
        digest: _digest,
        saved: _saved!,
        onSettingsChanged: _onSettingsChanged,
      ),
    ];
    return Scaffold(
      appBar: AppBar(
        title: const Text('Redunator'),
        actions: [
          if (!hasMapsApiKey)
            const Padding(
              padding: EdgeInsets.only(right: 12),
              child: Chip(label: Text('No Maps key')),
            ),
        ],
      ),
      body: IndexedStack(index: _tab, children: screens),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _tab,
        type: BottomNavigationBarType.fixed,
        onTap: (i) {
          unawaited(_sound.playTap());
          setState(() => _tab = i);
        },
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.map), label: 'Map'),
          BottomNavigationBarItem(icon: Icon(Icons.search), label: 'Query'),
          BottomNavigationBarItem(icon: Icon(Icons.bookmark), label: 'Saved'),
          BottomNavigationBarItem(icon: Icon(Icons.route), label: 'Route'),
          BottomNavigationBarItem(
            icon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}
