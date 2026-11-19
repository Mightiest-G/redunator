// Saved intersections (Pro): persisted locally in SharedPreferences
// AND mirrored to the backend via the existing /user_alerts endpoints
// (one alert per saved camera, threshold MEDIUM, radius 2000 m, named
// after the intersection).
//
// The local list is the source of truth. The server sync is
// best-effort: if the backend is unreachable or the device token is
// not minted yet, the save still succeeds locally and the sync is
// retried on the next load(). Unsaving deletes the server alert when
// we know its id; a missing id just means it was never synced.
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';
import '../api/models.dart';

class SavedIntersection {
  SavedIntersection({
    required this.cameraId,
    required this.intersection,
    required this.latitude,
    required this.longitude,
    this.serverAlertId,
  });

  final String cameraId;
  final String intersection;
  final double? latitude;
  final double? longitude;

  /// Id of the /user_alerts row mirroring this save, or null when it
  /// was never synced to the server.
  final int? serverAlertId;

  Map<String, dynamic> toJson() => {
    'camera_id': cameraId,
    'intersection': intersection,
    'latitude': latitude,
    'longitude': longitude,
    'server_alert_id': serverAlertId,
  };

  factory SavedIntersection.fromJson(Map<String, dynamic> json) =>
      SavedIntersection(
        cameraId: (json['camera_id'] ?? '').toString(),
        intersection: (json['intersection'] ?? '').toString(),
        latitude: (json['latitude'] as num?)?.toDouble(),
        longitude: (json['longitude'] as num?)?.toDouble(),
        serverAlertId: (json['server_alert_id'] as num?)?.toInt(),
      );

  SavedIntersection copyWith({int? serverAlertId}) => SavedIntersection(
    cameraId: cameraId,
    intersection: intersection,
    latitude: latitude,
    longitude: longitude,
    serverAlertId: serverAlertId ?? this.serverAlertId,
  );
}

class SavedService extends ChangeNotifier {
  SavedService({this.client});

  /// Backend client for the /user_alerts mirror. Public and mutable
  /// so the app can re-point it when the base URL changes (the client
  /// is rebuilt in _HomePageState) — same pattern as
  /// SubscriptionService.client. Null until the app sets it; local
  /// saves work without it.
  RedunatorApiClient? client;

  /// SharedPreferences key for the saved list. Public so the morning
  /// digest can read the same persisted list from the background
  /// isolate (the in-memory list is not available there).
  static const String prefsKey = 'saved_intersections';

  static const String _kSaved = prefsKey;

  /// Radius (m) of the server-side alert mirroring a saved camera.
  static const double serverAlertRadiusM = 2000;

  List<SavedIntersection> _saved = [];

  List<SavedIntersection> get saved => List.unmodifiable(_saved);

  bool isSaved(String cameraId) => _saved.any((s) => s.cameraId == cameraId);

  SavedIntersection? get(String cameraId) {
    for (final s in _saved) {
      if (s.cameraId == cameraId) return s;
    }
    return null;
  }

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_kSaved);
    if (raw != null) {
      try {
        final list = (jsonDecode(raw) as List)
            .map((e) => SavedIntersection.fromJson(e as Map<String, dynamic>))
            .where((s) => s.cameraId.isNotEmpty)
            .toList();
        _saved = list;
      } catch (_) {
        _saved = [];
      }
    }
    notifyListeners();
    // Best-effort: mirror any saves that never reached the server.
    await _syncMissing();
  }

  /// Saves a camera. Local first (never fails on the network), then
  /// a best-effort server mirror. A no-op when already saved.
  Future<void> save(Intersection cam) async {
    if (isSaved(cam.cameraId)) return;
    final entry = SavedIntersection(
      cameraId: cam.cameraId,
      intersection: cam.intersection,
      latitude: cam.latitude,
      longitude: cam.longitude,
    );
    _saved.add(entry);
    await _persist();
    notifyListeners();
    await _syncOne(entry);
  }

  /// Removes a camera from the saved list and, when we know its
  /// server alert id, deletes the mirror. Server failures are
  /// ignored — the local removal is what the user asked for.
  Future<void> unsave(String cameraId) async {
    final entry = get(cameraId);
    if (entry == null) return;
    _saved.removeWhere((s) => s.cameraId == cameraId);
    await _persist();
    notifyListeners();
    final alertId = entry.serverAlertId;
    final api = client;
    if (alertId != null && api != null) {
      try {
        await api.deleteUserAlert(alertId);
      } catch (_) {
        // The mirror is gone locally; a stale server row is harmless
        // (listUserAlerts filters active_only by default).
      }
    }
  }

  Future<void> _persist() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _kSaved,
      jsonEncode(_saved.map((s) => s.toJson()).toList()),
    );
  }

  /// Creates the server alert mirror for one saved camera and records
  /// its id. Cameras without coordinates cannot have a server alert
  /// (the endpoint needs lat/lon) and stay local-only. Failures are
  /// swallowed: the local save already stands.
  Future<void> _syncOne(SavedIntersection entry) async {
    final api = client;
    if (api == null) return;
    if (entry.latitude == null || entry.longitude == null) return;
    try {
      final alert = await api.createUserAlert(
        latitude: entry.latitude!,
        longitude: entry.longitude!,
        radiusM: serverAlertRadiusM,
        threshold: 'MEDIUM',
        name: entry.intersection,
      );
      _replace(entry.copyWith(serverAlertId: alert.id));
      await _persist();
    } catch (_) {
      // Best-effort only; retried on the next load().
    }
  }

  Future<void> _syncMissing() async {
    for (final entry in List<SavedIntersection>.from(_saved)) {
      if (entry.serverAlertId == null) {
        await _syncOne(entry);
      }
    }
  }

  void _replace(SavedIntersection updated) {
    final i = _saved.indexWhere((s) => s.cameraId == updated.cameraId);
    if (i >= 0) _saved[i] = updated;
  }
}
