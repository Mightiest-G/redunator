// Area watch (Exclusive): watch a neighbourhood, not just one camera.
// A watch area is a named circle on the map (added by long-pressing
// the map). The background check evaluates EVERY camera inside each
// watched area and alerts when any of them is HIGH today — even when
// you are nowhere near it. That is the difference from saved
// intersections (one camera) and from proximity alerts (where you are
// right now).
//
// Areas are persisted locally in SharedPreferences (the background
// isolate reads them directly, like the saved list). No server
// changes: the check reuses the existing camera list + batch
// predictions the app already fetches.
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/models.dart';
import '../api/routing.dart';

/// Watch-area radii offered in the UI (metres).
const List<double> kWatchAreaRadiusOptions = [500, 1000, 2000];

/// Default radius for a new watch area.
const double kDefaultWatchAreaRadiusM = 1000;

class WatchArea {
  WatchArea({
    required this.id,
    required this.name,
    required this.latitude,
    required this.longitude,
    this.radiusM = kDefaultWatchAreaRadiusM,
  });

  /// Stable id (milliseconds since epoch at creation).
  final String id;
  final String name;
  final double latitude;
  final double longitude;
  final double radiusM;

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'latitude': latitude,
    'longitude': longitude,
    'radius_m': radiusM,
  };

  factory WatchArea.fromJson(Map<String, dynamic> json) => WatchArea(
    id: (json['id'] ?? '').toString(),
    name: (json['name'] ?? 'Watch area').toString(),
    latitude: (json['latitude'] as num? ?? 0).toDouble(),
    longitude: (json['longitude'] as num? ?? 0).toDouble(),
    radiusM: (json['radius_m'] as num? ?? kDefaultWatchAreaRadiusM)
        .toDouble(),
  );
}

/// One area with at least one HIGH camera inside it, for one
/// background run.
class AreaAlert {
  AreaAlert({required this.area, required this.highCameras});

  final WatchArea area;

  /// HIGH cameras inside the area, highest daily prediction first.
  final List<Prediction> highCameras;
}

/// Pure: cameras whose coordinates fall inside [area].
List<Intersection> camerasInArea(
  List<Intersection> cameras,
  WatchArea area,
) {
  final center = LatLngPoint(area.latitude, area.longitude);
  return cameras.where((cam) {
    if (cam.latitude == null || cam.longitude == null) return false;
    return RouteService.distanceM(
          center,
          LatLngPoint(cam.latitude!, cam.longitude!),
        ) <=
        area.radiusM;
  }).toList();
}

/// Pure: for each area, the HIGH cameras inside it (from a batch
/// prediction map). Areas with no HIGH camera produce no alert.
/// At most one alert per area per run — the background dedupe (one
/// per area per day) lives in the caller.
List<AreaAlert> areaAlertTargets({
  required List<WatchArea> areas,
  required List<Intersection> cameras,
  required Map<String, Prediction> predictions,
}) {
  final alerts = <AreaAlert>[];
  for (final area in areas) {
    final inside = camerasInArea(cameras, area);
    final high = <Prediction>[];
    for (final cam in inside) {
      final pred = predictions[cam.cameraId];
      if (pred != null && pred.riskLevel == 'HIGH') high.add(pred);
    }
    if (high.isEmpty) continue;
    high.sort(
      (a, b) => b.dailyPrediction.compareTo(a.dailyPrediction),
    );
    alerts.add(AreaAlert(area: area, highCameras: high));
  }
  return alerts;
}

/// Notification body for one area alert. Lists the worst camera and
/// how many more are HIGH in the same area.
String buildAreaAlertText(AreaAlert alert) {
  final worst = alert.highCameras.first;
  final name = worst.intersection ?? worst.cameraId;
  final extra = alert.highCameras.length - 1;
  final more = extra > 0 ? ' (+$extra more HIGH in this area)' : '';
  return 'HIGH risk in ${alert.area.name}: $name — '
      '${worst.dailyPrediction.toStringAsFixed(1)}/day$more. Estimates.';
}

/// At most one area alert per area per calendar day (device local
/// date). Same pattern as the background camera dedupe.
class AreaAlertDedupe {
  AreaAlertDedupe(this._prefs);

  final SharedPreferences _prefs;

  static String _key(String areaId) => 'area_alert_last_$areaId';

  bool shouldAlert(String areaId, DateTime now) =>
      _prefs.getString(_key(areaId)) !=
      _dateKey(now);

  Future<void> recordAlert(String areaId, DateTime now) async {
    await _prefs.setString(_key(areaId), _dateKey(now));
  }

  static String _dateKey(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
}

class AreaWatchService extends ChangeNotifier {
  AreaWatchService();

  /// SharedPreferences key for the watch-area list. Public so the
  /// background isolate can read the same persisted list.
  static const String prefsKey = 'watch_areas';

  List<WatchArea> _areas = [];

  List<WatchArea> get areas => List.unmodifiable(_areas);

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(prefsKey);
    if (raw != null) {
      try {
        _areas = (jsonDecode(raw) as List)
            .map((e) => WatchArea.fromJson(e as Map<String, dynamic>))
            .where((a) => a.id.isNotEmpty)
            .toList();
      } catch (_) {
        _areas = [];
      }
    }
    notifyListeners();
  }

  static List<WatchArea> readPersisted(SharedPreferences prefs) {
    final raw = prefs.getString(prefsKey);
    if (raw == null) return [];
    try {
      return (jsonDecode(raw) as List)
          .map((e) => WatchArea.fromJson(e as Map<String, dynamic>))
          .where((a) => a.id.isNotEmpty)
          .toList();
    } catch (_) {
      return [];
    }
  }

  /// Adds a watch area centred on [latitude]/[longitude].
  Future<WatchArea> add({
    required String name,
    required double latitude,
    required double longitude,
    double radiusM = kDefaultWatchAreaRadiusM,
  }) async {
    final area = WatchArea(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      name: name.trim().isEmpty ? 'Watch area' : name.trim(),
      latitude: latitude,
      longitude: longitude,
      radiusM: radiusM,
    );
    _areas.add(area);
    await _persist();
    notifyListeners();
    return area;
  }

  Future<void> remove(String id) async {
    _areas.removeWhere((a) => a.id == id);
    await _persist();
    notifyListeners();
  }

  Future<void> _persist() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      prefsKey,
      jsonEncode(_areas.map((a) => a.toJson()).toList()),
    );
  }
}
