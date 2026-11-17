// Offline cache (SDD FR12): the last fetched intersections +
// predictions are stored on device (shared_preferences) so the map
// still works with no network. Cached data is ALWAYS labelled with
// its age ("Cached data — 3 h old") and never presented as live.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../api/models.dart';

class CachedData {
  CachedData({
    required this.intersections,
    required this.predictions,
    required this.fetchedAt,
  });
  final List<Intersection> intersections;
  final Map<String, Prediction> predictions; // keyed by camera_id
  final DateTime fetchedAt;

  int get ageMinutes => DateTime.now().difference(fetchedAt).inMinutes;

  String get ageLabel {
    final m = ageMinutes;
    if (m < 1) return 'just now';
    if (m < 60) return '$m min ago';
    final h = m ~/ 60;
    if (h < 24) return '$h h ago';
    return '${h ~/ 24} d ago';
  }
}

class CacheService {
  static const String _kIntersections = 'cache_intersections';
  static const String _kPredictions = 'cache_predictions';
  static const String _kFetchedAt = 'cache_fetched_at';

  Future<void> save(
    List<Intersection> intersections,
    Map<String, Prediction> predictions,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _kIntersections,
      jsonEncode(intersections.map((e) => e.toJson()).toList()),
    );
    await prefs.setString(
      _kPredictions,
      jsonEncode(
        predictions.map((k, v) => MapEntry(k, v.toJson())),
      ),
    );
    await prefs.setString(_kFetchedAt, DateTime.now().toIso8601String());
  }

  Future<CachedData?> load() async {
    final prefs = await SharedPreferences.getInstance();
    final rawI = prefs.getString(_kIntersections);
    final rawP = prefs.getString(_kPredictions);
    final rawT = prefs.getString(_kFetchedAt);
    if (rawI == null || rawT == null) return null;
    try {
      final intersections = (jsonDecode(rawI) as List)
          .map((e) => Intersection.fromJson(e as Map<String, dynamic>))
          .toList();
      final predictions = <String, Prediction>{};
      if (rawP != null) {
        final Map<String, dynamic> map =
            jsonDecode(rawP) as Map<String, dynamic>;
        for (final entry in map.entries) {
          predictions[entry.key] =
              Prediction.fromJson(entry.value as Map<String, dynamic>);
        }
      }
      return CachedData(
        intersections: intersections,
        predictions: predictions,
        fetchedAt: DateTime.parse(rawT),
      );
    } on FormatException {
      return null;
    }
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_kIntersections);
    await prefs.remove(_kPredictions);
    await prefs.remove(_kFetchedAt);
  }
}
