// Widget tests for the Pro background alerts + morning digest
// Settings sections: free users see the upsell, Pro users see the
// toggles and the test-digest button.
import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:in_app_purchase/in_app_purchase.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/screens/settings_screen.dart';
import 'package:redunator/services/area_watch_service.dart';
import 'package:redunator/services/background_alerts.dart';
import 'package:redunator/services/morning_digest.dart';
import 'package:redunator/services/proximity_service.dart';
import 'package:redunator/services/saved_service.dart';
import 'package:redunator/services/settings_service.dart';
import 'package:redunator/services/sound_service.dart';
import 'package:redunator/services/spoken_alert_service.dart';
import 'package:redunator/services/widget_service.dart';
import 'package:redunator/services/subscription_service.dart';
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

Future<void> _pumpSettings(
  WidgetTester tester, {
  required SubscriptionService subscription,
}) async {
  tester.view.physicalSize = const Size(800, 6000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final client = RedunatorApiClient(
    baseUrl: 'https://example.invalid',
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
          subscription: subscription,
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
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('Settings shows the background perks upsell to free users',
      (tester) async {
    await _pumpSettings(tester, subscription: SubscriptionService());
    expect(find.text('Background alerts'), findsWidgets);
    expect(
      find.textContaining('Pro keeps watching even when the app is closed'),
      findsOneWidget,
    );
    expect(find.text('Morning digest'), findsWidgets);
    expect(
      find.textContaining('daily 07:00 summary'),
      findsOneWidget,
    );
    // Free users get no toggles for these perks.
    expect(find.widgetWithText(SwitchListTile, 'Background alerts'),
        findsNothing);
    expect(
      find.widgetWithText(SwitchListTile, 'Morning digest'),
      findsNothing,
    );
    expect(find.text('Send test digest now'), findsNothing);
  });

  testWidgets('Settings shows the background perks toggles when Pro',
      (tester) async {
    SharedPreferences.setMockInitialValues({'is_pro': true});
    final sub = await _proSubscription();
    addTearDown(sub.dispose);
    await _pumpSettings(tester, subscription: sub);
    expect(
      find.widgetWithText(SwitchListTile, 'Background alerts'),
      findsOneWidget,
    );
    expect(
      find.widgetWithText(SwitchListTile, 'Morning digest'),
      findsOneWidget,
    );
    expect(find.text('Send test digest now'), findsOneWidget);
    expect(
      find.text('Enable notifications & location'),
      findsOneWidget,
    );
    expect(find.text('Pro is active'), findsOneWidget);
  });
}
