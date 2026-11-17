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

void main() {
  runApp(const RedunatorApp());
}

class RedunatorApp extends StatelessWidget {
  const RedunatorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Redunator',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.red),
        useMaterial3: true,
      ),
      home: const _HomePage(),
    );
  }
}

class _HomePage extends StatefulWidget {
  const _HomePage();

  @override
  State<_HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<_HomePage> {
  AppSettings? _settings;
  RedunatorApiClient? _client;
  final ProximityService _proximity = ProximityService();
  int _tab = 0;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    final settings = await AppSettings.load();
    if (!mounted) return;
    setState(() {
      _settings = settings;
      _client = _buildClient(settings);
    });
  }

  RedunatorApiClient _buildClient(AppSettings s) => RedunatorApiClient(
        baseUrl: s.baseUrl,
        userId: s.userId,
      );

  void _onSettingsChanged() {
    final s = _settings;
    if (s == null) return;
    setState(() {
      _client = _buildClient(s);
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
    final settings = _settings;
    final client = _client;
    if (settings == null || client == null) {
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
