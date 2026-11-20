// Subscription tests: Pro gating is driven only by backend-verified
// purchases. A fake store + mock HTTP client stand in for Play Billing
// and the backend; the tests prove the rules that matter:
//  - a purchased product with a backend "valid: true" grants Pro;
//  - a backend "valid: false" denies it;
//  - no client / backend-unavailable / "not configured" never grants Pro;
//  - error purchases surface a message; pending/canceled do nothing;
//  - buy/restore delegate to the store.
import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:in_app_purchase/in_app_purchase.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/services/subscription_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

class FakeBillingStore implements BillingStore {
  FakeBillingStore({
    this.available = true,
    this.products = const [],
  });

  bool available;
  List<ProductDetails> products;
  final purchaseController =
      StreamController<List<PurchaseDetails>>.broadcast();
  bool buyCalled = false;
  bool restoreCalled = false;
  bool completeCalled = false;
  Set<String>? queriedIds;

  @override
  Future<bool> isAvailable() async => available;

  @override
  Future<ProductDetailsResponse> queryProductDetails(Set<String> ids) async {
    queriedIds = ids;
    return ProductDetailsResponse(
      productDetails: products,
      notFoundIDs: [],
    );
  }

  @override
  Future<bool> buyNonConsumable(PurchaseParam param) async {
    buyCalled = true;
    return true;
  }

  @override
  Stream<List<PurchaseDetails>> get purchaseStream =>
      purchaseController.stream;

  @override
  Future<void> completePurchase(PurchaseDetails purchase) async {
    completeCalled = true;
  }

  @override
  Future<void> restorePurchases() async {
    restoreCalled = true;
  }
}

ProductDetails monthlyProduct() => ProductDetails(
      id: kProMonthlyProductId,
      title: 'Pro — monthly',
      description: 'Redunator Pro, billed monthly.',
      price: '\$2.99',
      rawPrice: 2.99,
      currencyCode: 'USD',
    );

ProductDetails exclusiveMonthlyProduct() => ProductDetails(
      id: kExclusiveMonthlyProductId,
      title: 'Exclusive — monthly',
      description: 'Redunator Exclusive, billed monthly.',
      price: '\$5.99',
      rawPrice: 5.99,
      currencyCode: 'USD',
    );

PurchaseDetails purchasedMonthly({bool pendingComplete = false}) {
  final p = PurchaseDetails(
    purchaseID: 'test-purchase-1',
    productID: kProMonthlyProductId,
    verificationData: PurchaseVerificationData(
      localVerificationData: 'local',
      serverVerificationData: 'test-token-123',
      source: 'google_play',
    ),
    transactionDate: DateTime.now().millisecondsSinceEpoch.toString(),
    status: PurchaseStatus.purchased,
  );
  p.pendingCompletePurchase = pendingComplete;
  return p;
}

PurchaseDetails purchasedExclusive() {
  final p = PurchaseDetails(
    purchaseID: 'test-purchase-2',
    productID: kExclusiveMonthlyProductId,
    verificationData: PurchaseVerificationData(
      localVerificationData: 'local',
      serverVerificationData: 'test-token-456',
      source: 'google_play',
    ),
    transactionDate: DateTime.now().millisecondsSinceEpoch.toString(),
    status: PurchaseStatus.purchased,
  );
  return p;
}

RedunatorApiClient verifyClient(Map<String, dynamic> verdict) =>
    verifyClientFor(kProMonthlyProductId, verdict);

RedunatorApiClient verifyClientFor(
  String productId,
  Map<String, dynamic> verdict,
) =>
    RedunatorApiClient(
      baseUrl: 'https://example.invalid',
      httpClient: MockClient((req) async {
        expect(req.url.path, '/subscriptions/verify');
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body['product_id'], productId);
        return http.Response(jsonEncode(verdict), 200);
      }),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('SubscriptionService', () {
    test('starts free, loads products from the store', () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(store: store);
      await sub.init();
      expect(sub.isPro, isFalse);
      expect(sub.storeAvailable, isTrue);
      expect(sub.productsLoaded, isTrue);
      expect(sub.products.map((p) => p.id), [kProMonthlyProductId]);
      sub.dispose();
    });

    test('store unavailable: no products, stays free', () async {
      final store = FakeBillingStore(available: false);
      final sub = SubscriptionService(store: store);
      await sub.init();
      expect(sub.storeAvailable, isFalse);
      expect(sub.isPro, isFalse);
      sub.dispose();
    });

    test('verified purchase grants Pro and completes the purchase',
        () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(
        store: store,
        client: verifyClient({'valid': true, 'product_id': kProMonthlyProductId}),
      );
      await sub.init();
      store.purchaseController.add([purchasedMonthly(pendingComplete: true)]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isPro, isTrue);
      expect(store.completeCalled, isTrue);
      // Pro survives a reload from the cached state.
      final reloaded = SubscriptionService(store: store);
      await reloaded.init();
      expect(reloaded.isPro, isTrue);
      sub.dispose();
      reloaded.dispose();
    });

    test('backend says invalid: purchase does NOT grant Pro', () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(
        store: store,
        client: verifyClient({'valid': false}),
      );
      await sub.init();
      store.purchaseController.add([purchasedMonthly()]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isPro, isFalse);
      expect(sub.lastError, isNotNull);
      sub.dispose();
    });

    test('verification not configured: purchase does NOT grant Pro',
        () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(
        store: store,
        client: verifyClient({
          'valid': null,
          'reason': 'play_verification_not_configured',
        }),
      );
      await sub.init();
      store.purchaseController.add([purchasedMonthly()]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isPro, isFalse);
      expect(sub.lastError, isNull);
      sub.dispose();
    });

    test('no client at all: purchase does NOT grant Pro', () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(store: store);
      await sub.init();
      store.purchaseController.add([purchasedMonthly()]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isPro, isFalse);
      sub.dispose();
    });

    test('error purchase surfaces a message, stays free', () async {
      final store = FakeBillingStore();
      final sub = SubscriptionService(store: store);
      await sub.init();
      final p = purchasedMonthly()..status = PurchaseStatus.error;
      p.error = IAPError(
        source: 'test',
        code: 'test_error',
        message: 'Payment declined',
      );
      store.purchaseController.add([p]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isPro, isFalse);
      expect(sub.lastError, contains('Payment declined'));
      sub.dispose();
    });

    test('pending and canceled purchases do nothing', () async {
      final store = FakeBillingStore();
      final sub = SubscriptionService(store: store);
      await sub.init();
      final pending = purchasedMonthly()..status = PurchaseStatus.pending;
      final canceled = purchasedMonthly()..status = PurchaseStatus.canceled;
      store.purchaseController.add([pending, canceled]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isPro, isFalse);
      expect(sub.lastError, isNull);
      sub.dispose();
    });

    test('buy and restore delegate to the store', () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(store: store);
      await sub.init();
      expect(await sub.buy(monthlyProduct()), isTrue);
      expect(store.buyCalled, isTrue);
      await sub.restore();
      expect(store.restoreCalled, isTrue);
      sub.dispose();
    });

    test('expired cached Pro is dropped on load', () async {
      SharedPreferences.setMockInitialValues({
        'is_pro': true,
        'pro_product_id': kProMonthlyProductId,
        'pro_purchase_token': 'old-token',
        'pro_expiry_ms':
            DateTime.now().subtract(const Duration(days: 1)).millisecondsSinceEpoch,
      });
      final sub = SubscriptionService(store: FakeBillingStore());
      await sub.init();
      expect(sub.isPro, isFalse);
      sub.dispose();
    });

    test('verified Exclusive purchase grants Exclusive (and Pro)', () async {
      final store =
          FakeBillingStore(products: [exclusiveMonthlyProduct()]);
      final sub = SubscriptionService(
        store: store,
        client: verifyClientFor(
          kExclusiveMonthlyProductId,
          {'valid': true, 'product_id': kExclusiveMonthlyProductId},
        ),
      );
      await sub.init();
      store.purchaseController.add([purchasedExclusive()]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.isExclusive, isTrue);
      expect(sub.isPro, isTrue);
      expect(sub.tier, SubscriptionTier.exclusive);
      // Exclusive survives a reload from the cached tier.
      final reloaded = SubscriptionService(store: store);
      await reloaded.init();
      expect(reloaded.isExclusive, isTrue);
      sub.dispose();
      reloaded.dispose();
    });

    test('a Pro purchase never downgrades an active Exclusive', () async {
      final store = FakeBillingStore(
        products: [monthlyProduct(), exclusiveMonthlyProduct()],
      );
      // This test sends two different product ids to the backend,
      // so the verify client accepts any of them.
      final sub = SubscriptionService(
        store: store,
        client: RedunatorApiClient(
          baseUrl: 'https://example.invalid',
          httpClient: MockClient((req) async {
            expect(req.url.path, '/subscriptions/verify');
            return http.Response(jsonEncode({'valid': true}), 200);
          }),
        ),
      );
      await sub.init();
      store.purchaseController.add([purchasedExclusive()]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.tier, SubscriptionTier.exclusive);
      // A restored/old Pro purchase arriving afterwards must not
      // drop the user back to Pro.
      store.purchaseController.add([purchasedMonthly()]);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(sub.tier, SubscriptionTier.exclusive);
      sub.dispose();
    });

    test('legacy is_pro bool migrates to the Pro tier', () async {
      SharedPreferences.setMockInitialValues({
        'is_pro': true,
        'pro_product_id': kProMonthlyProductId,
        'pro_purchase_token': 'legacy-token',
        'pro_expiry_ms': DateTime.now()
            .add(const Duration(days: 30))
            .millisecondsSinceEpoch,
      });
      final sub = SubscriptionService(store: FakeBillingStore());
      await sub.init();
      expect(sub.tier, SubscriptionTier.pro);
      expect(sub.isPro, isTrue);
      expect(sub.isExclusive, isFalse);
      sub.dispose();
    });

    test('init queries all four products', () async {
      final store = FakeBillingStore(products: [monthlyProduct()]);
      final sub = SubscriptionService(store: store);
      await sub.init();
      expect(store.queriedIds, kAllProductIds);
      sub.dispose();
    });

    test('tierForProduct maps every product id', () {
      expect(tierForProduct(kProMonthlyProductId), SubscriptionTier.pro);
      expect(tierForProduct(kProYearlyProductId), SubscriptionTier.pro);
      expect(
        tierForProduct(kExclusiveMonthlyProductId),
        SubscriptionTier.exclusive,
      );
      expect(
        tierForProduct(kExclusiveYearlyProductId),
        SubscriptionTier.exclusive,
      );
      expect(tierForProduct('something-else'), SubscriptionTier.free);
    });

    test('saveLimitForTier: Free 3, Pro 10, Exclusive unlimited', () {
      expect(saveLimitForTier(SubscriptionTier.free), 3);
      expect(saveLimitForTier(SubscriptionTier.pro), 10);
      expect(saveLimitForTier(SubscriptionTier.exclusive), isNull);
    });
  });
}
