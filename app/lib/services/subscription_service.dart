// Redunator subscriptions (Google Play Billing via in_app_purchase).
//
// TIERS (Gabriel's call, 2026-11-19): Free / Pro / Exclusive.
//   Free — map + intersection queries + up to 3 saved intersections.
//   Pro — everything Free has, plus route planning, proximity alerts,
//     saved intersections with spike alerts (up to 10), 7- and 30-day
//     risk trends, safest time to leave, custom alert distance,
//     background alerts (15-minute WorkManager check while the app is
//     closed) and the 07:00 morning risk digest.
//   Exclusive — everything Pro has, plus unlimited saves, area watch
//     (alerts for ANY high-risk camera in a watched area, even when
//     you are not there), route comparison (alternative routes ranked
//     by risk), the weekly risk report + Sunday digest, spoken alerts,
//     and the home-screen risk widget.
//
// The gate lives in one place (SubscriptionService.tier, with the
// isPro / isExclusive conveniences read by the screens and the
// background services) — changing the split is a one-line change
// per screen.
//
// PRODUCT IDS — these exact IDs must be created in Google Play Console
// (two subscriptions, two base plans each) before any purchase can
// work. Until then the store reports them as not found, the UI says
// subscriptions are not available yet, and the tier stays free.
// Nothing here fakes a purchase.
//
// VERIFICATION: a store purchase is only trusted after the backend
// confirms it (POST /subscriptions/verify, which checks the purchase
// token against the Google Play Developer API for ANY product id —
// the backend is product-agnostic, so Exclusive needs no server
// change). The app never grants a tier from the store receipt alone.
// When the backend says verification is not configured (no Play
// credentials on the server yet), the tier stays free — that is the
// safe default, not a bug.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:in_app_purchase/in_app_purchase.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';

/// The three subscription tiers, lowest first.
enum SubscriptionTier { free, pro, exclusive }

/// Play Console product IDs for the Pro subscription (base plans).
const String kProMonthlyProductId = 'redunator_pro_monthly';
const String kProYearlyProductId = 'redunator_pro_yearly';
const Set<String> kProProductIds = {kProMonthlyProductId, kProYearlyProductId};

/// Play Console product IDs for the Exclusive subscription (base plans).
const String kExclusiveMonthlyProductId = 'redunator_exclusive_monthly';
const String kExclusiveYearlyProductId = 'redunator_exclusive_yearly';
const Set<String> kExclusiveProductIds = {
  kExclusiveMonthlyProductId,
  kExclusiveYearlyProductId,
};

/// Every product the store is queried for.
const Set<String> kAllProductIds = {
  ...kProProductIds,
  ...kExclusiveProductIds,
};

/// The tier a product id buys. Unknown ids buy nothing (free).
SubscriptionTier tierForProduct(String productId) {
  if (kExclusiveProductIds.contains(productId)) {
    return SubscriptionTier.exclusive;
  }
  if (kProProductIds.contains(productId)) {
    return SubscriptionTier.pro;
  }
  return SubscriptionTier.free;
}

/// Max saved intersections per tier. Null = unlimited. Free users get
/// 3 saves (new — saves were Pro-only before this change). Existing
/// saves above a new cap are grandfathered: only NEW saves are
/// blocked, nothing is ever deleted for a downgrade.
int? saveLimitForTier(SubscriptionTier tier) => switch (tier) {
  SubscriptionTier.free => 3,
  SubscriptionTier.pro => 10,
  SubscriptionTier.exclusive => null,
};

/// Thin seam over the billing plugin so tests can use a fake store.
/// The real implementation delegates to InAppPurchase.instance.
abstract class BillingStore {
  Future<bool> isAvailable();
  Future<ProductDetailsResponse> queryProductDetails(Set<String> ids);
  Future<bool> buyNonConsumable(PurchaseParam param);
  Stream<List<PurchaseDetails>> get purchaseStream;
  Future<void> completePurchase(PurchaseDetails purchase);
  Future<void> restorePurchases();
}

class RealBillingStore implements BillingStore {
  RealBillingStore([InAppPurchase? store])
    : _store = store ?? InAppPurchase.instance;
  final InAppPurchase _store;

  @override
  Future<bool> isAvailable() => _store.isAvailable();

  @override
  Future<ProductDetailsResponse> queryProductDetails(Set<String> ids) =>
      _store.queryProductDetails(ids);

  @override
  Future<bool> buyNonConsumable(PurchaseParam param) =>
      _store.buyNonConsumable(purchaseParam: param);

  @override
  Stream<List<PurchaseDetails>> get purchaseStream => _store.purchaseStream;

  @override
  Future<void> completePurchase(PurchaseDetails purchase) =>
      _store.completePurchase(purchase);

  @override
  Future<void> restorePurchases() => _store.restorePurchases();
}

/// Result of asking the backend to verify a purchase token.
enum PurchaseVerification { verified, invalid, unavailable }

class SubscriptionService extends ChangeNotifier {
  SubscriptionService({
    BillingStore? store,
    this.client,
    this.deviceId = 'anonymous',
  }) : _store = store ?? RealBillingStore();

  final BillingStore _store;

  /// Backend client for purchase verification. Public and mutable so
  /// the app can re-point the service when the base URL changes (the
  /// client is rebuilt in _HomePageState). Null until the app sets it.
  RedunatorApiClient? client;

  /// Device id sent with verification. Set by the app startup wiring
  /// to the TokenService device UUID (the same id the JWT is minted
  /// for); 'anonymous' until then — verification still works, the
  /// token is what matters.
  String deviceId;

  /// Current tier. Public so screens can read it directly.
  static const String tierPrefsKey = 'subscription_tier';

  /// Legacy keys (pre-tier build): the bool is read once for
  /// migration and then superseded by [tierPrefsKey].
  static const String _kIsPro = 'is_pro';
  static const String _kProductId = 'pro_product_id';
  static const String _kPurchaseToken = 'pro_purchase_token';
  static const String _kExpiryMs = 'pro_expiry_ms';

    /// Test-only tier override, set at build time with
    /// --dart-define=REDUNATOR_TEST_TIER=pro|exclusive. Off by default
    /// and never set by a release build: it lets a test APK run the
    /// paid background features (digest, background alerts, widget)
    /// on a device without a store purchase, so they can be verified
    /// before Play Console products exist. In-memory only — nothing is
    /// written to prefs, so installing the real build afterwards
    /// restores Free and the override can never leak into a purchase.
    static const String _testTier = String.fromEnvironment(
          'REDUNATOR_TEST_TIER',
          defaultValue: 'none',
        );

    SubscriptionTier _tier = SubscriptionTier.free;

    /// Current tier, with the test override applied.
    SubscriptionTier get tier {
          if (_testTier == 'exclusive') return SubscriptionTier.exclusive;
          if (_testTier == 'pro') return SubscriptionTier.pro;
          return _tier;
    }

    /// True for Pro and Exclusive (every paid tier).
    bool get isPro => tier != SubscriptionTier.free;

    /// True only for Exclusive.
    bool get isExclusive => tier == SubscriptionTier.exclusive;

  /// Products the store returned (empty until init() queries them).
  List<ProductDetails> products = [];

  /// True when the store answered at all (false = billing unavailable
  /// on this device, e.g. no Play Store).
  bool storeAvailable = false;

  /// True once init() has queried the store (even if it found nothing).
  bool productsLoaded = false;

  /// Last user-facing error (purchase failed, verification failed…).
  String? lastError;

  StreamSubscription<List<PurchaseDetails>>? _purchaseSub;
  bool _initialized = false;

  /// Loads the cached tier, subscribes to the purchase stream and
  /// queries the store for the subscription products. Safe to call
  /// repeatedly.
  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;
    await _loadCachedTier();
    _purchaseSub = _store.purchaseStream.listen(_onPurchases, onError: (_) {});
    try {
      storeAvailable = await _store.isAvailable();
    } catch (_) {
      storeAvailable = false;
    }
    if (storeAvailable) {
      await refreshProducts();
    }
    // Re-validate a cached subscription against the backend on launch.
    if (_tier != SubscriptionTier.free) await revalidate();
    notifyListeners();
  }

  Future<void> refreshProducts() async {
    try {
      final resp = await _store.queryProductDetails(kAllProductIds);
      products = resp.productDetails;
      if (resp.error != null) lastError = resp.error!.message;
    } catch (e) {
      lastError = 'Could not load subscription products: $e';
    }
    productsLoaded = true;
    notifyListeners();
  }

  /// Buys a product. The result arrives via the purchase stream.
  /// Returns false only if the store refused to start the purchase.
  Future<bool> buy(ProductDetails product) async {
    lastError = null;
    try {
      return await _store.buyNonConsumable(
        PurchaseParam(productDetails: product),
      );
    } catch (e) {
      lastError = 'Purchase could not start: $e';
      notifyListeners();
      return false;
    }
  }

  /// Replays past purchases through the purchase stream (also the way
  /// a reinstall recovers a tier).
  Future<void> restore() async {
    lastError = null;
    try {
      await _store.restorePurchases();
    } catch (e) {
      lastError = 'Restore failed: $e';
      notifyListeners();
    }
  }

  /// Re-checks the cached subscription with the backend. Clears the
  /// tier only when the backend explicitly says the purchase is
  /// invalid. A network/401 failure leaves the cached state alone
  /// (it was real once); "verification not configured" leaves the
  /// tier alone too.
  Future<void> revalidate() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString(_kPurchaseToken);
    final productId = prefs.getString(_kProductId);
    if (token == null || productId == null) return;
    final result = await _verifyWithBackend(productId, token);
    if (result == PurchaseVerification.invalid) {
      await _setTier(SubscriptionTier.free, force: true);
    }
  }

  Future<void> _onPurchases(List<PurchaseDetails> purchases) async {
    for (final purchase in purchases) {
      switch (purchase.status) {
        case PurchaseStatus.purchased:
        case PurchaseStatus.restored:
          await _handleVerifiedPurchase(purchase);
        case PurchaseStatus.error:
          lastError = purchase.error?.message ?? 'Purchase failed. Try again.';
          notifyListeners();
        case PurchaseStatus.pending:
        case PurchaseStatus.canceled:
          break;
      }
      if (purchase.pendingCompletePurchase) {
        try {
          await _store.completePurchase(purchase);
        } catch (_) {}
      }
    }
  }

  Future<void> _handleVerifiedPurchase(PurchaseDetails purchase) async {
    final tier = tierForProduct(purchase.productID);
    if (tier == SubscriptionTier.free) return;
    final token = purchase.verificationData.serverVerificationData;
    final result = await _verifyWithBackend(purchase.productID, token);
    if (result == PurchaseVerification.verified) {
      await _setTier(tier, productId: purchase.productID, token: token);
    } else if (result == PurchaseVerification.invalid) {
      lastError = 'Google Play did not confirm this purchase.';
      notifyListeners();
    }
    // unavailable: verification is not configured yet — the tier
    // stays as it was and lastError stays null (this is expected
    // before Play is set up).
  }

  /// Asks the backend to verify a purchase token. Returns unavailable
  /// when there is no client, the network fails, or the backend says
  /// verification is not configured (valid == null).
  Future<PurchaseVerification> _verifyWithBackend(
    String productId,
    String purchaseToken,
  ) async {
    final api = client;
    if (api == null) return PurchaseVerification.unavailable;
    try {
      final json = await api.verifySubscription(
        productId: productId,
        purchaseToken: purchaseToken,
        deviceId: deviceId,
      );
      final valid = json['valid'];
      if (valid == null) return PurchaseVerification.unavailable;
      if (valid == true) {
        final expiry = json['expiry'];
        if (expiry is String) {
          final prefs = await SharedPreferences.getInstance();
          final ms = DateTime.tryParse(expiry)?.millisecondsSinceEpoch;
          if (ms != null) await prefs.setInt(_kExpiryMs, ms);
        }
        return PurchaseVerification.verified;
      }
      return PurchaseVerification.invalid;
    } catch (_) {
      return PurchaseVerification.unavailable;
    }
  }

  Future<void> _loadCachedTier() async {
    final prefs = await SharedPreferences.getInstance();
    final expiryMs = prefs.getInt(_kExpiryMs);
    final expired =
        expiryMs != null && expiryMs < DateTime.now().millisecondsSinceEpoch;
    final stored = prefs.getString(tierPrefsKey);
    if (stored != null) {
      _tier = SubscriptionTier.values.firstWhere(
        (t) => t.name == stored,
        orElse: () => SubscriptionTier.free,
      );
    } else {
      // One-time migration from the pre-tier build: the old bool +
      // stored product id become a tier.
      final wasPro = prefs.getBool(_kIsPro) ?? false;
      final productId = prefs.getString(_kProductId);
      _tier = wasPro
          ? tierForProduct(productId ?? '')
          : SubscriptionTier.free;
      if (wasPro && _tier == SubscriptionTier.free) {
        // Old Pro state with no (or an unknown) product id: keep Pro
        // rather than silently dropping a paid user.
        _tier = SubscriptionTier.pro;
      }
      await prefs.setString(tierPrefsKey, _tier.name);
    }
    if (expired) {
      _tier = SubscriptionTier.free;
      await prefs.setString(tierPrefsKey, _tier.name);
    }
  }

  /// Applies a tier change. A purchase never downgrades: a Pro
  /// purchase arriving while Exclusive is active (e.g. a restore of
  /// an old Pro purchase) is ignored. Only expiry and an explicit
  /// backend "invalid" (force) clear the tier down.
  Future<void> _setTier(
    SubscriptionTier value, {
    String? productId,
    String? token,
    bool force = false,
  }) async {
    if (!force && value != SubscriptionTier.free && value.index < _tier.index) {
      return;
    }
    final prefs = await SharedPreferences.getInstance();
    _tier = value;
    await prefs.setString(tierPrefsKey, value.name);
    if (value != SubscriptionTier.free) {
      if (productId != null) await prefs.setString(_kProductId, productId);
      if (token != null) await prefs.setString(_kPurchaseToken, token);
    } else {
      await prefs.remove(_kProductId);
      await prefs.remove(_kPurchaseToken);
      await prefs.remove(_kExpiryMs);
    }
    notifyListeners();
  }

  /// Test/dev helper: build with a fake store without touching storage.
  static SubscriptionService withStore(BillingStore store) =>
      SubscriptionService(store: store);

  @override
  void dispose() {
    unawaited(_purchaseSub?.cancel());
    super.dispose();
  }
}
