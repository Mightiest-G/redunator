// Redunator Pro subscriptions (Google Play Billing via in_app_purchase).
//
// WHAT PRO UNLOCKS (Gabriel's call, 2026-11-18; extended 2026-11-19):
// Route planning, proximity alerts, saved intersections + spike
// alerts, risk trends, safest time to leave, custom alert distance,
// background alerts (15-minute WorkManager check while the app is
// closed) and the 07:00 morning risk digest.
// Map + Query stay free. The gate lives in one place
// (SubscriptionService.isPro, read by RouteScreen, MapScreen,
// SavedScreen and the detail sheet) — changing the split is a
// one-line change per screen.
//
// PRODUCT IDS — these exact IDs must be created in Google Play Console
// (one subscription, two base plans) before any purchase can work.
// Until then the store reports them as not found, the UI says Pro is
// "coming soon", and isPro stays false. Nothing here fakes a purchase.
//
// VERIFICATION: a store purchase is only trusted after the backend
// confirms it (POST /subscriptions/verify, which checks the purchase
// token against the Google Play Developer API). The app never grants
// Pro from the store receipt alone. When the backend says verification
// is not configured (no Play credentials on the server yet), Pro stays
// off — that is the safe default, not a bug.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:in_app_purchase/in_app_purchase.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';

/// Play Console product IDs for the Pro subscription (base plans).
const String kProMonthlyProductId = 'redunator_pro_monthly';
const String kProYearlyProductId = 'redunator_pro_yearly';
const Set<String> kProProductIds = {kProMonthlyProductId, kProYearlyProductId};

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

  static const String _kIsPro = 'is_pro';
  static const String _kProProductId = 'pro_product_id';
  static const String _kProPurchaseToken = 'pro_purchase_token';
  static const String _kProExpiryMs = 'pro_expiry_ms';

  bool _isPro = false;
  bool get isPro => _isPro;

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

  /// Loads cached Pro state, subscribes to the purchase stream and
  /// queries the store for the Pro products. Safe to call repeatedly.
  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;
    await _loadCachedPro();
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
    if (_isPro) await revalidate();
    notifyListeners();
  }

  Future<void> refreshProducts() async {
    try {
      final resp = await _store.queryProductDetails(kProProductIds);
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
  /// a reinstall recovers Pro).
  Future<void> restore() async {
    lastError = null;
    try {
      await _store.restorePurchases();
    } catch (e) {
      lastError = 'Restore failed: $e';
      notifyListeners();
    }
  }

  /// Re-checks the cached subscription with the backend. Clears Pro
  /// only when the backend explicitly says the purchase is invalid.
  /// A network/401 failure leaves the cached state alone (it was real
  /// once); "verification not configured" leaves Pro off.
  Future<void> revalidate() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString(_kProPurchaseToken);
    final productId = prefs.getString(_kProProductId);
    if (token == null || productId == null) return;
    final result = await _verifyWithBackend(productId, token);
    if (result == PurchaseVerification.invalid) {
      await _setPro(false);
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
    if (!kProProductIds.contains(purchase.productID)) return;
    final token = purchase.verificationData.serverVerificationData;
    final result = await _verifyWithBackend(purchase.productID, token);
    if (result == PurchaseVerification.verified) {
      await _setPro(true, productId: purchase.productID, token: token);
    } else if (result == PurchaseVerification.invalid) {
      lastError = 'Google Play did not confirm this purchase.';
      notifyListeners();
    }
    // unavailable: verification is not configured yet — Pro stays off
    // and lastError stays null (this is expected before Play is set up).
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
          if (ms != null) await prefs.setInt(_kProExpiryMs, ms);
        }
        return PurchaseVerification.verified;
      }
      return PurchaseVerification.invalid;
    } catch (_) {
      return PurchaseVerification.unavailable;
    }
  }

  Future<void> _loadCachedPro() async {
    final prefs = await SharedPreferences.getInstance();
    final expiryMs = prefs.getInt(_kProExpiryMs);
    final expired =
        expiryMs != null && expiryMs < DateTime.now().millisecondsSinceEpoch;
    _isPro = (prefs.getBool(_kIsPro) ?? false) && !expired;
    if (expired) await prefs.setBool(_kIsPro, false);
  }

  Future<void> _setPro(bool value, {String? productId, String? token}) async {
    final prefs = await SharedPreferences.getInstance();
    _isPro = value;
    await prefs.setBool(_kIsPro, value);
    if (value) {
      if (productId != null) await prefs.setString(_kProProductId, productId);
      if (token != null) await prefs.setString(_kProPurchaseToken, token);
    } else {
      await prefs.remove(_kProProductId);
      await prefs.remove(_kProPurchaseToken);
      await prefs.remove(_kProExpiryMs);
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
