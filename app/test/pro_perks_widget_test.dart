// Widget tests for the Pro perks: the trend section, the Saved tab
// gate + spike flag, and the Pro alert-distance setting (locked for
// free users, a segmented control for Pro).
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:in_app_purchase/in_app_purchase.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/screens/saved_screen.dart';
import 'package:redunator/screens/settings_screen.dart';
import 'package:redunator/services/background_alerts.dart';
import 'package:redunator/services/morning_digest.dart';
import 'package:redunator/services/proximity_service.dart';
import 'package:redunator/services/saved_service.dart';
import 'package:redunator/services/settings_service.dart';
import 'package:redunator/services/sound_service.dart';
import 'package:redunator/services/subscription_service.dart';
import 'package:redunator/widgets/trend_section.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeBillingStore implements BillingStore {
  @override
  Future<bool> isAvailable() async => true;

  @override
  Future<ProductDetailsResponse> queryProductDetails(Set<String> ids) async =>
      ProductDetailsResponse(productDetails: [], notFoundIDs: []);

  @override
  Future<bool> buyNonConsumable(PurchaseParam param) async => false;

  @override
  Stream<List<PurchaseDetails>> get purchaseStream => const Stream.empty();

  @override
  Future<void> completePurchase(PurchaseDetails purchase) async {}

  @override
  Future<void> restorePurchases() async {}
}

Future<SubscriptionService> _proSubscription() async {
  final sub = SubscriptionService(store: _FakeBillingStore());
  await sub.init();
  expect(sub.isPro, isTrue);
  return sub;
}

Map<String, dynamic> _predictionJson({String cameraId = '1413'}) => {
  'camera_id': cameraId,
  'intersection': 'LAKE SHORE DR AND BELMONT',
  'latitude': 41.940241,
  'longitude': -87.639639,
  'date': '2026-10-15',
  'timestamp': '2026-10-15T12:00:00',
  'daily_prediction': 53.0095,
  'daily_source': 'model',
  'risk_score': 1.0,
  'risk_level': 'HIGH',
  'confidence': 0.9,
  'reasons': ['High historical volume at this camera.'],
  'hourly': List.generate(
    24,
    (h) => {
      'hour': h,
      'predicted_violations': h == 15 ? 4.8194 : 1.0,
      'band': h == 15 ? 'HIGH' : 'HIGH',
      'risk_score': h == 15 ? 0.1377 : 0.0286,
      'estimate': true,
    },
  ),
  'hourly_estimate_note': 'Hourly values are ESTIMATES.',
  'hourly_method': 'model',
  'hourly_sum': 53.0095,
  'reconciliation_error': 0.0,
  'model_version': 1,
  'nearest_distance_m': null,
};

Map<String, dynamic> _trendsJson() {
  // 90 days at 2.0/day observed: 7-day mean 2.0, so today's 53.0 is a
  // spike (>1.5x).
  final days = List.generate(
    90,
    (i) => {
      'date': '2026-08-0${(i % 9) + 1}',
      'observed_violations': 2,
      'predicted_violations': 2.0,
    },
  );
  return {
    'camera_id': '1413',
    'intersection': 'LAKE SHORE DR AND BELMONT',
    'granularity': 'daily',
    'note': '',
    'start_date': '2026-08-01',
    'end_date': '2026-10-29',
    'days': days,
    'summary': {
      'n_days': 90,
      'mean_observed': 2.0,
      'mean_predicted': 2.0,
      'total_observed': 180,
    },
  };
}

RedunatorApiClient _savedScreenClient() => RedunatorApiClient(
  baseUrl: 'https://example.invalid',
  httpClient: MockClient((req) async {
    if (req.url.path == '/predict_risk/batch') {
      return http.Response(
        jsonEncode({
          'results': [_predictionJson()],
          'count': 1,
          'errors': 0,
        }),
        200,
      );
    }
    if (req.url.path == '/violation_trends') {
      return http.Response(jsonEncode(_trendsJson()), 200);
    }
    return http.Response('not found', 404);
  }),
);

ViolationTrends _trends({required List<double> observed}) => ViolationTrends(
  cameraId: '1413',
  intersection: 'LAKE SHORE DR AND BELMONT',
  granularity: 'daily',
  note: '',
  startDate: '2026-08-31',
  endDate: '2026-10-29',
  days: observed
      .asMap()
      .entries
      .map(
        (e) => TrendDay(
          date: '2026-10-${(e.key + 1).toString().padLeft(2, '0')}',
          observedViolations: e.value,
          predictedViolations: e.value,
        ),
      )
      .toList(),
  nDays: observed.length,
  meanObserved: 3.0,
  meanPredicted: 3.0,
  totalObserved: 180,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('TrendSection shows 7/30-day direction and percent change', (
    tester,
  ) async {
    // First 30 days at 2.0, next 23 at 4.0, last 7 at 5.0:
    // 7-day +25% (5 vs 4), 30-day +112% (4.23 vs 2.0).
    final trends = _trends(
      observed: [
        ...List.filled(30, 2.0),
        ...List.filled(23, 4.0),
        ...List.filled(7, 5.0),
      ],
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: TrendSection(trends: trends)),
      ),
    );
    await tester.pump();
    expect(find.text('7-day trend'), findsOneWidget);
    expect(find.textContaining('+25%'), findsOneWidget);
    expect(find.text('30-day trend'), findsOneWidget);
    expect(find.textContaining('+112%'), findsOneWidget);
    expect(
      find.text('Daily observed violations, last 30 days'),
      findsOneWidget,
    );
  });

  testWidgets('Saved tab shows the upsell to free users', (tester) async {
    final saved = SavedService();
    await saved.load();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SavedScreen(
            client: _savedScreenClient(),
            settings: AppSettings.withValues(),
            sound: SoundService(),
            subscription: SubscriptionService(),
            saved: saved,
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('Redunator Pro'), findsOneWidget);
    expect(find.text('Saved intersections'), findsNothing);
  });

  testWidgets('Saved tab lists a saved camera with a spike flag', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({
      'is_pro': true,
      'saved_intersections': jsonEncode([
        {
          'camera_id': '1413',
          'intersection': 'LAKE SHORE DR AND BELMONT',
          'latitude': 41.940241,
          'longitude': -87.639639,
          'server_alert_id': null,
        },
      ]),
    });
    final sub = await _proSubscription();
    addTearDown(sub.dispose);
    final saved = SavedService();
    await saved.load();
    expect(saved.saved.length, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SavedScreen(
            client: _savedScreenClient(),
            settings: AppSettings.withValues(),
            sound: SoundService(),
            subscription: sub,
            saved: saved,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('LAKE SHORE DR AND BELMONT'), findsOneWidget);
    // Today 53.0/day vs a 7-day mean of 2.0/day is a spike.
    expect(find.text('SPIKE'), findsOneWidget);
    expect(find.textContaining('53.0/day'), findsOneWidget);
    expect(find.byIcon(Icons.delete_outline), findsOneWidget);
  });

  testWidgets('Settings shows the Pro alert distance locked for free', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(800, 4000);
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
            onSettingsChanged: () {},
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('Pro alert distance'), findsOneWidget);
    expect(find.textContaining('A Pro feature'), findsOneWidget);
  });

  testWidgets('Settings shows the Pro distance choices when Pro', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({'is_pro': true});
    final sub = await _proSubscription();
    addTearDown(sub.dispose);
    tester.view.physicalSize = const Size(800, 4000);
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
            subscription: sub,
            backgroundAlerts: BackgroundAlertsService(),
            digest: MorningDigestService(),
            saved: SavedService(),
            onSettingsChanged: () {},
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('Pro alert distance'), findsOneWidget);
    // Default Pro distance is 250 m; the segmented button shows all
    // four options.
    expect(find.text('250 m'), findsOneWidget);
    expect(find.text('100 m'), findsOneWidget);
    expect(find.text('1.00 km'), findsOneWidget);
    expect(find.text('Pro is active'), findsOneWidget);
  });
}
