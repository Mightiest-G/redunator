// Widget tests: missing-key screen, hourly chart, settings screen.
// NOTE (apk-19): the Maps key is injected at build time now
// (MAPS_API_KEY env / --dart-define, see lib/config.dart) and no
// longer lives in the repo. Run the suite with
// `--dart-define=MAPS_API_KEY=test-key`, or the Settings test below
// fails — it asserts the key shows as configured. The missing-key
// explanation itself is tested standalone below (MissingKeyView).
import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/screens/settings_screen.dart';
import 'package:redunator/services/background_alerts.dart';
import 'package:redunator/services/morning_digest.dart';
import 'package:redunator/services/proximity_service.dart';
import 'package:redunator/services/saved_service.dart';
import 'package:redunator/services/settings_service.dart';
import 'package:redunator/services/area_watch_service.dart';
import 'package:redunator/services/sound_service.dart';
import 'package:redunator/services/spoken_alert_service.dart';
import 'package:redunator/services/widget_service.dart';
import 'package:redunator/services/subscription_service.dart';
import 'package:redunator/widgets/hourly_chart.dart';
import 'package:redunator/widgets/missing_key_view.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('Missing-key explanation names the fix, not a blank map',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: MissingKeyView()),
      ),
    );
    await tester.pump();
    expect(find.text('Google Maps API key missing'), findsOneWidget);
    expect(find.textContaining('console.cloud.google.com'), findsOneWidget);
    // The fix is build-time injection now, not pasting a key into
    // the repo (apk-19: the committed key was rotated out).
    expect(find.textContaining('MAPS_API_KEY'), findsOneWidget);
    expect(find.textContaining('GOOGLE_MAPS_API_KEY'), findsOneWidget);
  });

  testWidgets('HourlyChart renders bands and the estimate label',
      (tester) async {
    final hourly = List.generate(
      24,
      (h) => HourlyEntry(
        hour: h,
        predictedViolations: h == 15 ? 4.82 : 0.5,
        band: h == 15 ? 'HIGH' : 'HIGH',
        riskScore: 0.14,
        estimate: true,
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: HourlyChart(hourly: hourly)),
      ),
    );
    await tester.pump();
    expect(find.textContaining('estimates from proxies'), findsOneWidget);
    expect(find.text('HIGH ≥0.5/hr'), findsOneWidget);
  });

  testWidgets('Settings screen shows defaults and key status', (tester) async {
    SharedPreferences.setMockInitialValues({});
    // Tall viewport so the whole ListView builds (its lower tiles are
    // otherwise lazily built only when scrolled into view).
    tester.view.physicalSize = const Size(800, 8000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final client = RedunatorApiClient(
      baseUrl: 'http://10.0.2.2:8000',
      httpClient: MockClient((req) async => http.Response('{}', 200)),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SettingsScreen(
            settings: AppSettings.withValues(),
            client: client,
            proximity: ProximityService(),
            sound: SoundService(),
            subscription: SubscriptionService(),
            backgroundAlerts: BackgroundAlertsService(),
            digest: MorningDigestService(),
            saved: SavedService(),
            areaWatch: AreaWatchService(),
            spoken: SpokenAlertService(),
            widgetService: WidgetService(),
            onSettingsChanged: () {},
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.textContaining('Alert distance: 300 m'), findsOneWidget);
    expect(find.textContaining('Risk threshold: 75%'), findsOneWidget);
    expect(find.textContaining('Backend base URL'), findsOneWidget);
    expect(find.textContaining('Tap sounds'), findsOneWidget);
    expect(find.textContaining('Redunator plans'), findsOneWidget);
    expect(
      find.textContaining('Google Maps API key configured'),
      findsOneWidget,
    );
    expect(find.textContaining('Background'), findsOneWidget);
  });
}
