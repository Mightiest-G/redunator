// Typed Dart client for the Redunator backend.
// Covers EVERY backend endpoint (see API_REFERENCE.md):
//   POST /predict_risk, POST /predict_risk/batch, GET /intersections,
//   GET /violation_trends, GET /live/risk, POST /user_report,
//   GET /user_report/{id}, GET/POST /user_alerts, DELETE /user_alerts/{id},
//   POST /user_feedback, GET/POST /user_settings, GET /health.
// Base URL is configurable (default is the live Cloud Run service).
// No mock data: tests inject a mock http.Client, the shipped app always hits the
// real backend (or shows an offline/cached state).
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

class RedunatorApiClient {
  RedunatorApiClient({
    required this.baseUrl,
    http.Client? httpClient,
    this.userId = 'anonymous',
    this.apiKey,
  }) : _http = httpClient ?? http.Client();

  /// Base URL without a trailing slash, e.g. the Cloud Run service URL.
  final String baseUrl;
  final http.Client _http;

  /// Sent as X-User-Id on user endpoints (backend falls back to the
  /// user_id body field, then "anonymous"). No account system exists.
  final String userId;

  /// Optional X-Api-Key. Only needed if the backend was started with
  /// REDUNATOR_API_KEY set (local-dev gate, not real auth).
  final String? apiKey;

  String get _base => baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;

  Map<String, String> get _headers {
    final h = <String, String>{
      'Content-Type': 'application/json',
      'X-User-Id': userId,
    };
    if (apiKey != null && apiKey!.isNotEmpty) h['X-Api-Key'] = apiKey!;
    return h;
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = Uri.parse(_base);
    return base.replace(
      path: '${base.path}$path',
      queryParameters: query,
    );
  }

  Future<Map<String, dynamic>> _getJson(Uri uri) async {
    final resp = await _http.get(uri, headers: _headers);
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      throw ApiException.fromBody(resp.statusCode, resp.body);
    }
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> _postJson(
    Uri uri,
    Map<String, dynamic> body,
  ) async {
    final resp = await _http.post(
      uri,
      headers: _headers,
      body: jsonEncode(body),
    );
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      throw ApiException.fromBody(resp.statusCode, resp.body);
    }
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  // ------------------------------------------------------------------
  // GET /health (exempt from rate limiting)
  // ------------------------------------------------------------------
  Future<Health> getHealth() async {
    final json = await _getJson(_uri('/health'));
    return Health.fromJson(json);
  }

  // ------------------------------------------------------------------
  // GET /app/latest — latest published APK release (update check).
  // Returns tag/build/download URL, or nulls + a reason when the
  // backend cannot reach GitHub (e.g. no token configured).
  // ------------------------------------------------------------------
  Future<LatestRelease> getLatestRelease() async {
    final json = await _getJson(_uri('/app/latest'));
    return LatestRelease.fromJson(json);
  }

  // ------------------------------------------------------------------
  // POST /predict_risk
  // Body: at least one of camera_id OR latitude+longitude.
  // NOTE: the date field is named `timestamp` (ISO-8601), NOT `date`.
  // ------------------------------------------------------------------
  Future<Prediction> predictRisk({
    String? cameraId,
    double? latitude,
    double? longitude,
    DateTime? timestamp,
    String? method,
    bool? demand,
    bool? slowness,
    String? banding,
  }) async {
    final body = <String, dynamic>{};
    if (cameraId != null) body['camera_id'] = cameraId;
    if (latitude != null) body['latitude'] = latitude;
    if (longitude != null) body['longitude'] = longitude;
    if (timestamp != null) body['timestamp'] = timestamp.toIso8601String();
    if (method != null) body['method'] = method;
    if (demand != null) body['demand'] = demand;
    if (slowness != null) body['slowness'] = slowness;
    if (banding != null) body['banding'] = banding;
    final json = await _postJson(_uri('/predict_risk'), body);
    return Prediction.fromJson(json);
  }

  // ------------------------------------------------------------------
  // POST /predict_risk/batch — 1..100 items; one bad item never
  // fails the batch (per-item errors land in BatchResult.errors).
  // ------------------------------------------------------------------
  Future<BatchResult> predictRiskBatch(
    List<Map<String, dynamic>> items,
  ) async {
    final json = await _postJson(_uri('/predict_risk/batch'), {'items': items});
    return BatchResult.fromJson(json);
  }

  // ------------------------------------------------------------------
  // GET /intersections
  // ------------------------------------------------------------------
  Future<List<Intersection>> getIntersections({
    String? query,
    double? minTypicalWeekdayPred,
    double? minTypicalWeekendPred,
    String? band,
    int limit = 300,
    int offset = 0,
  }) async {
    final q = <String, String>{
      'limit': limit.toString(),
      'offset': offset.toString(),
    };
    if (query != null && query.isNotEmpty) q['q'] = query;
    if (minTypicalWeekdayPred != null) {
      q['min_typical_weekday_pred'] = minTypicalWeekdayPred.toString();
    }
    if (minTypicalWeekendPred != null) {
      q['min_typical_weekend_pred'] = minTypicalWeekendPred.toString();
    }
    if (band != null) q['band'] = band;
    final resp = await _http.get(_uri('/intersections', q), headers: _headers);
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      throw ApiException.fromBody(resp.statusCode, resp.body);
    }
    final List<dynamic> list = jsonDecode(resp.body) as List<dynamic>;
    return list
        .map((e) => Intersection.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  // ------------------------------------------------------------------
  // GET /violation_trends — DAILY granularity only.
  // ------------------------------------------------------------------
  Future<ViolationTrends> getViolationTrends({
    String? cameraId,
    String? intersection,
    DateTime? startDate,
    DateTime? endDate,
  }) async {
    final q = <String, String>{};
    if (cameraId != null) q['camera_id'] = cameraId;
    if (intersection != null) q['intersection'] = intersection;
    if (startDate != null) {
      q['start_date'] = _dateOnly(startDate);
    }
    if (endDate != null) q['end_date'] = _dateOnly(endDate);
    final json = await _getJson(_uri('/violation_trends', q));
    return ViolationTrends.fromJson(json);
  }

  // ------------------------------------------------------------------
  // GET /live/risk — nearest camera to lat/lon.
  // `at` must be timezone-aware if given; defaults to now.
  // ------------------------------------------------------------------
  Future<LiveRisk> getLiveRisk({
    required double latitude,
    required double longitude,
    DateTime? at,
    int? maxDistanceM,
  }) async {
    final q = <String, String>{
      'lat': latitude.toString(),
      'lon': longitude.toString(),
    };
    if (at != null) q['at'] = at.toIso8601String();
    if (maxDistanceM != null) q['max_distance_m'] = maxDistanceM.toString();
    final json = await _getJson(_uri('/live/risk', q));
    return LiveRisk.fromJson(json);
  }

  // ------------------------------------------------------------------
  // POST /user_report — "Report Spike" (201). At least one of
  // camera_id or lat+lon is required.
  // ------------------------------------------------------------------
  Future<UserReport> reportSpike({
    String? cameraId,
    double? latitude,
    double? longitude,
    DateTime? timestamp,
    String? note,
  }) async {
    final body = <String, dynamic>{'user_id': userId};
    if (cameraId != null) body['camera_id'] = cameraId;
    if (latitude != null) body['lat'] = latitude;
    if (longitude != null) body['lon'] = longitude;
    if (timestamp != null) body['timestamp'] = timestamp.toIso8601String();
    if (note != null) body['note'] = note;
    final json = await _postJson(_uri('/user_report'), body);
    return UserReport.fromJson(json);
  }

  Future<UserReport> getUserReport(int id) async {
    final json = await _getJson(_uri('/user_report/$id'));
    return UserReport.fromJson(json);
  }

  // ------------------------------------------------------------------
  // GET /user_alerts, POST /user_alerts, DELETE /user_alerts/{id}
  // ------------------------------------------------------------------
  Future<List<UserAlert>> listUserAlerts({bool activeOnly = true}) async {
    final json = await _getJson(_uri('/user_alerts', {
      'user_id': userId,
      'active_only': activeOnly.toString(),
    }));
    final List<dynamic> list = (json['alerts'] as List?) ?? [];
    return list
        .map((e) => UserAlert.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<UserAlert> createUserAlert({
    required double latitude,
    required double longitude,
    double radiusM = 2000,
    String threshold = 'MEDIUM',
    double? thresholdValue,
    String? name,
  }) async {
    final body = <String, dynamic>{
      'user_id': userId,
      'lat': latitude,
      'lon': longitude,
      'radius_m': radiusM,
      'threshold': threshold,
    };
    if (thresholdValue != null) body['threshold_value'] = thresholdValue;
    if (name != null) body['name'] = name;
    final json = await _postJson(_uri('/user_alerts'), body);
    return UserAlert.fromJson(json);
  }

  Future<UserAlert> deleteUserAlert(int id) async {
    final resp = await _http.delete(_uri('/user_alerts/$id'), headers: _headers);
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      throw ApiException.fromBody(resp.statusCode, resp.body);
    }
    return UserAlert.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ------------------------------------------------------------------
  // POST /user_feedback — 201. `text` required; `message` is an
  // accepted alias on the backend (we send `text`).
  // ------------------------------------------------------------------
  Future<Map<String, dynamic>> sendFeedback(UserFeedback feedback) async {
    final body = feedback.toJson()..['user_id'] = userId;
    return _postJson(_uri('/user_feedback'), body);
  }

  // ------------------------------------------------------------------
  // GET /user_settings, POST /user_settings (merged over defaults).
  // ------------------------------------------------------------------
  Future<UserSettings> getUserSettings() async {
    final json = await _getJson(_uri('/user_settings', {'user_id': userId}));
    return UserSettings.fromJson(json);
  }

  Future<UserSettings> updateUserSettings(Map<String, dynamic> settings) async {
    final body = <String, dynamic>{'settings': settings, 'user_id': userId};
    final json = await _postJson(_uri('/user_settings'), body);
    return UserSettings.fromJson(json);
  }

  void close() => _http.close();

  static String _dateOnly(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
}
