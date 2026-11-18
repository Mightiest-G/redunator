// Redunator — Chicago red-light-camera ticket-risk predictor.
// Tabs: Map (home), Query, Route, Settings.
// The backend client is rebuilt whenever the base URL / user ID
// changes in Settings. No mock data anywhere in the shipped app:
// every number comes from the backend or from the on-device cache
// (labelled as cached with its age).
import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import 'api/client.dart';
import 'config.dart';
import 'screens/map_screen.dart';
import 'screens/query_screen.dart';
import 'screens/route_screen.dart';
import 'screens/settings_screen.dart';
import 'services/proximity_service.dart';
import 'services/settings_service.dart';
import 'services/update_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Settings are loaded before the app starts so the theme (dark mode)
  // is right on the very first frame — no light flash on a dark phone.
  final settings = await AppSettings.load();
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
  int _tab = 0;
  bool _updateChecked = false;

  @override
  void initState() {
    super.initState();
    _client = _buildClient(widget.settings);
    // One update check per launch, after the first frame (so the
    // dialog has a Navigator to show in).
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkForUpdate());
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

  RedunatorApiClient _buildClient(AppSettings s) => RedunatorApiClient(
        baseUrl: s.baseUrl,
        userId: s.userId,
      );

  void _onSettingsChanged() {
    setState(() {
      _client = _buildClient(widget.settings);
    });
  }

  @override
  void dispose() {
    _proximity.dispose();
    _client?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.settings;
    final client = _client;
    if (client == null) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    final screens = <Widget>[
      MapScreen(
        key: ValueKey('map:${client.baseUrl}'),
        settings: settings,
        client: client,
        proximity: _proximity,
        onBaseUrlChanged: _onSettingsChanged,
      ),
      QueryScreen(client: client),
      RouteScreen(client: client, proximity: _proximity),
      SettingsScreen(
        settings: settings,
        client: client,
        proximity: _proximity,
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
        onTap: (i) => setState(() => _tab = i),
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.map), label: 'Map'),
          BottomNavigationBarItem(icon: Icon(Icons.search), label: 'Query'),
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
