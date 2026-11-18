// Device auth: the app has no user accounts, so identity is a
// per-device JWT (backend/auth.py).
//
// - One UUID per device, generated once and stored on the device
//   (SharedPreferences `device_id`). It never changes, never leaves
//   the device except in the POST /auth/device mint call.
// - POST /auth/device mints a 30-day bearer token; the token is
//   cached on the device (SharedPreferences `auth_token` +
//   `auth_token_expiry_ms`) and attached to the client, which sends
//   it as `Authorization: Bearer <token>` on the protected user
//   endpoints. The token's `sub` claim IS the user id — the old
//   X-User-Id header is ignored by the backend and no longer used
//   for identity anywhere in the app.
// - On a 401 the client calls back here (onUnauthorized) to mint a
//   fresh token and retries the request once. If minting fails
//   (backend has no JWT secret yet → 503), the request fails with
//   401 and the UI shows its normal error — the app never retries
//   in a loop and never blocks the free (public) endpoints.
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';

class TokenService {
  static const String _kDeviceId = 'device_id';
  static const String _kAuthToken = 'auth_token';
  static const String _kAuthTokenExpiryMs = 'auth_token_expiry_ms';

  /// 5-minute safety margin: a token expiring within this window is
  /// treated as expired so it never dies mid-request.
  static const int _kExpiryMarginMs = 5 * 60 * 1000;

  String? _deviceId;

  /// The device UUID (null until load() has run).
  String? get deviceId => _deviceId;

  /// Loads the stored device UUID, or generates and stores one.
  /// Call once at startup, before ensureToken().
  Future<String> load() async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString(_kDeviceId);
    if (id == null || id.isEmpty) {
      id = _newUuidV4();
      await prefs.setString(_kDeviceId, id);
    }
    _deviceId = id;
    return id;
  }

  /// Returns a valid token for [client], minting one when the cached
  /// token is missing or near expiry. Sets it on the client.
  /// Returns null when the backend cannot mint one.
  Future<String?> ensureToken(RedunatorApiClient client) async {
    final prefs = await SharedPreferences.getInstance();
    final cached = prefs.getString(_kAuthToken);
    final expiryMs = prefs.getInt(_kAuthTokenExpiryMs);
    final now = DateTime.now().millisecondsSinceEpoch;
    if (cached != null &&
        cached.isNotEmpty &&
        expiryMs != null &&
        expiryMs > now + _kExpiryMarginMs) {
      client.authToken = cached;
      return cached;
    }
    return refreshToken(client);
  }

  /// Forces a fresh token mint, even if a cached one exists. Used
  /// after a 401. Returns null when minting fails (never throws).
  Future<String?> refreshToken(RedunatorApiClient client) async {
    final id = _deviceId ?? await load();
    try {
      final json = await client.issueDeviceToken(id);
      final token = json['access_token'] as String?;
      final expiresIn = json['expires_in'] as int?;
      if (token == null || token.isEmpty) return null;
      final prefs = await SharedPreferences.getInstance();
      final expiryMs = DateTime.now().millisecondsSinceEpoch +
          (expiresIn ?? 2592000) * 1000;
      await prefs.setString(_kAuthToken, token);
      await prefs.setInt(_kAuthTokenExpiryMs, expiryMs);
      client.authToken = token;
      return token;
    } catch (_) {
      return null;
    }
  }

  /// Test helper: build with a fixed device id without storage.
  static TokenService withDeviceId(String id) {
    final s = TokenService();
    s._deviceId = id;
    return s;
  }

  /// RFC 4122 version-4 UUID from a secure random source. No extra
  /// dependency for one UUID.
  static String _newUuidV4() {
    final r = Random.secure();
    final bytes = List<int>.generate(16, (_) => r.nextInt(256));
    bytes[6] = (bytes[6] & 0x0F) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3F) | 0x80; // variant 10xx
    final hex =
        bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-'
        '${hex.substring(20)}';
  }
}
