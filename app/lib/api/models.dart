// Typed models for every Redunator backend endpoint.
// Field names/shapes follow backend/routers/*.py and
// app/API_REFERENCE.md exactly. No mock data lives in the app.
import 'dart:convert';

/// Error body returned by the backend everywhere:
/// {"error": ..., "message": ...}
class ApiException implements Exception {
  ApiException(this.statusCode, this.error, this.message);
  final int statusCode;
  final String error;
  final String message;

  @override
  String toString() => 'ApiException($statusCode $error: $message)';

  factory ApiException.fromBody(int status, String body) {
    try {
      final Map<String, dynamic> json = jsonDecode(body) as Map<String, dynamic>;
      return ApiException(
        status,
        (json['error'] ?? 'error').toString(),
        (json['message'] ?? body).toString(),
      );
    } on FormatException {
      return ApiException(status, 'error', body);
    }
  }
}

class HourlyEntry {
  HourlyEntry({
    required this.hour,
    required this.predictedViolations,
    required this.band,
    required this.riskScore,
    required this.estimate,
  });
  final int hour;
  final double predictedViolations;
  final String band; // LOW / MEDIUM / HIGH (hourly bands)
  final double riskScore; // hourly_prediction / 35 (backend scale)
  final bool estimate; // always true — hourly values are estimates

  factory HourlyEntry.fromJson(Map<String, dynamic> json) => HourlyEntry(
        hour: (json['hour'] as num).toInt(),
        predictedViolations: (json['predicted_violations'] as num).toDouble(),
        band: (json['band'] ?? '').toString(),
        riskScore: (json['risk_score'] as num).toDouble(),
        estimate: json['estimate'] as bool? ?? true,
      );

  Map<String, dynamic> toJson() => {
        'hour': hour,
        'predicted_violations': predictedViolations,
        'band': band,
        'risk_score': riskScore,
        'estimate': estimate,
      };
}

class Prediction {
  Prediction({
    required this.cameraId,
    required this.intersection,
    required this.latitude,
    required this.longitude,
    required this.date,
    required this.timestamp,
    required this.dailyPrediction,
    required this.dailySource,
    required this.riskScore,
    required this.riskLevel,
    required this.confidence,
    required this.reasons,
    required this.hourly,
    required this.hourlyEstimateNote,
    required this.hourlyMethod,
    required this.hourlySum,
    required this.reconciliationError,
    required this.modelVersion,
    required this.nearestDistanceM,
  });
  final String cameraId;
  final String? intersection;
  final double? latitude;
  final double? longitude;
  final String date; // YYYY-MM-DD
  final String? timestamp;
  final double dailyPrediction;
  final String dailySource; // model | pre_test_mean | citywide_mean
  final double riskScore; // clip(daily_prediction / 35, 0, 1) — backend value
  final String riskLevel; // LOW / MEDIUM / HIGH (daily band)
  final double confidence; // heuristic, not a calibrated probability
  final List<String> reasons;
  final List<HourlyEntry> hourly; // 24 entries; every one estimate: true
  final String hourlyEstimateNote;
  final String hourlyMethod;
  final double hourlySum;
  final double reconciliationError;
  final int? modelVersion;
  final double? nearestDistanceM;

  factory Prediction.fromJson(Map<String, dynamic> json) => Prediction(
        cameraId: (json['camera_id'] ?? '').toString(),
        intersection: json['intersection'] as String?,
        latitude: (json['latitude'] as num?)?.toDouble(),
        longitude: (json['longitude'] as num?)?.toDouble(),
        date: (json['date'] ?? '').toString(),
        timestamp: json['timestamp'] as String?,
        dailyPrediction: (json['daily_prediction'] as num).toDouble(),
        dailySource: (json['daily_source'] ?? '').toString(),
        riskScore: (json['risk_score'] as num).toDouble(),
        riskLevel: (json['risk_level'] ?? '').toString(),
        confidence: (json['confidence'] as num).toDouble(),
        reasons: ((json['reasons'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        hourly: ((json['hourly'] as List?) ?? [])
            .map((e) => HourlyEntry.fromJson(e as Map<String, dynamic>))
            .toList(),
        hourlyEstimateNote: (json['hourly_estimate_note'] ?? '').toString(),
        hourlyMethod: (json['hourly_method'] ?? '').toString(),
        hourlySum: (json['hourly_sum'] as num? ?? 0).toDouble(),
        reconciliationError:
            (json['reconciliation_error'] as num? ?? 0).toDouble(),
        modelVersion: (json['model_version'] as num?)?.toInt(),
        nearestDistanceM: (json['nearest_distance_m'] as num?)?.toDouble(),
      );

  Map<String, dynamic> toJson() => {
        'camera_id': cameraId,
        'intersection': intersection,
        'latitude': latitude,
        'longitude': longitude,
        'date': date,
        'timestamp': timestamp,
        'daily_prediction': dailyPrediction,
        'daily_source': dailySource,
        'risk_score': riskScore,
        'risk_level': riskLevel,
        'confidence': confidence,
        'reasons': reasons,
        'hourly': hourly.map((e) => e.toJson()).toList(),
        'hourly_estimate_note': hourlyEstimateNote,
        'hourly_method': hourlyMethod,
        'hourly_sum': hourlySum,
        'reconciliation_error': reconciliationError,
        'model_version': modelVersion,
        'nearest_distance_m': nearestDistanceM,
      };

  /// The hourly entry for a given hour (0-23), or null if absent.
  HourlyEntry? hourEntry(int hour) {
    for (final e in hourly) {
      if (e.hour == hour) return e;
    }
    return null;
  }
}

class BatchResult {
  BatchResult({required this.results, required this.count, required this.errors});
  final List<Prediction> results;
  final int count;
  final int errors;

  factory BatchResult.fromJson(Map<String, dynamic> json) {
    final List<Prediction> ok = [];
    var errCount = (json['errors'] as num? ?? 0).toInt();
    for (final item in (json['results'] as List? ?? [])) {
      final Map<String, dynamic> m = item as Map<String, dynamic>;
      if (m.containsKey('daily_prediction')) {
        ok.add(Prediction.fromJson(m));
      } else {
        errCount += 1; // per-item error object {index, camera_id, error, message}
      }
    }
    return BatchResult(
      results: ok,
      count: (json['count'] as num? ?? ok.length).toInt(),
      errors: errCount,
    );
  }
}

class Intersection {
  Intersection({
    required this.cameraId,
    required this.intersection,
    required this.latitude,
    required this.longitude,
    required this.typicalWeekdayPred,
    required this.typicalWeekendPred,
    required this.typicalWeekdayBand,
    required this.typicalWeekendBand,
    required this.typicalWeekdayScore,
    required this.typicalWeekendScore,
    required this.actualTestMean,
    required this.camMeanPreTest,
  });
  final String cameraId;
  final String intersection;
  final double? latitude;
  final double? longitude;
  final double typicalWeekdayPred;
  final double typicalWeekendPred;
  final String typicalWeekdayBand;
  final String typicalWeekendBand;
  final double typicalWeekdayScore;
  final double typicalWeekendScore;
  final double? actualTestMean;
  final double? camMeanPreTest;

  factory Intersection.fromJson(Map<String, dynamic> json) => Intersection(
        cameraId: (json['camera_id'] ?? '').toString(),
        intersection: (json['intersection'] ?? '').toString(),
        latitude: (json['latitude'] as num?)?.toDouble(),
        longitude: (json['longitude'] as num?)?.toDouble(),
        typicalWeekdayPred:
            (json['typical_weekday_pred'] as num? ?? 0).toDouble(),
        typicalWeekendPred:
            (json['typical_weekend_pred'] as num? ?? 0).toDouble(),
        typicalWeekdayBand: (json['typical_weekday_band'] ?? '').toString(),
        typicalWeekendBand: (json['typical_weekend_band'] ?? '').toString(),
        typicalWeekdayScore:
            (json['typical_weekday_score'] as num? ?? 0).toDouble(),
        typicalWeekendScore:
            (json['typical_weekend_score'] as num? ?? 0).toDouble(),
        actualTestMean: (json['actual_test_mean'] as num?)?.toDouble(),
        camMeanPreTest: (json['cam_mean_pre_test'] as num?)?.toDouble(),
      );

  Map<String, dynamic> toJson() => {
        'camera_id': cameraId,
        'intersection': intersection,
        'latitude': latitude,
        'longitude': longitude,
        'typical_weekday_pred': typicalWeekdayPred,
        'typical_weekend_pred': typicalWeekendPred,
        'typical_weekday_band': typicalWeekdayBand,
        'typical_weekend_band': typicalWeekendBand,
        'typical_weekday_score': typicalWeekdayScore,
        'typical_weekend_score': typicalWeekendScore,
        'actual_test_mean': actualTestMean,
        'cam_mean_pre_test': camMeanPreTest,
      };
}

class TrendDay {
  TrendDay({
    required this.date,
    required this.observedViolations,
    required this.predictedViolations,
  });
  final String date;
  final double? observedViolations;
  final double? predictedViolations;

  factory TrendDay.fromJson(Map<String, dynamic> json) => TrendDay(
        date: (json['date'] ?? '').toString(),
        observedViolations: (json['observed_violations'] as num?)?.toDouble(),
        predictedViolations:
            (json['predicted_violations'] as num?)?.toDouble(),
      );
}

class ViolationTrends {
  ViolationTrends({
    required this.cameraId,
    required this.intersection,
    required this.granularity,
    required this.note,
    required this.startDate,
    required this.endDate,
    required this.days,
    required this.nDays,
    required this.meanObserved,
    required this.meanPredicted,
    required this.totalObserved,
  });
  final String cameraId;
  final String? intersection;
  final String granularity; // "daily" only — no hourly values
  final String note;
  final String startDate;
  final String endDate;
  final List<TrendDay> days;
  final int nDays;
  final double? meanObserved;
  final double? meanPredicted;
  final double? totalObserved;

  factory ViolationTrends.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> summary =
        (json['summary'] as Map?)?.cast<String, dynamic>() ?? {};
    return ViolationTrends(
      cameraId: (json['camera_id'] ?? '').toString(),
      intersection: json['intersection'] as String?,
      granularity: (json['granularity'] ?? 'daily').toString(),
      note: (json['note'] ?? '').toString(),
      startDate: (json['start_date'] ?? '').toString(),
      endDate: (json['end_date'] ?? '').toString(),
      days: ((json['days'] as List?) ?? [])
          .map((e) => TrendDay.fromJson(e as Map<String, dynamic>))
          .toList(),
      nDays: (summary['n_days'] as num? ?? 0).toInt(),
      meanObserved: (summary['mean_observed'] as num?)?.toDouble(),
      meanPredicted: (summary['mean_predicted'] as num?)?.toDouble(),
      totalObserved: (summary['total_observed'] as num?)?.toDouble(),
    );
  }
}

class LiveRisk {
  LiveRisk({
    required this.cameraId,
    required this.intersection,
    required this.distanceM,
    required this.latitude,
    required this.longitude,
    required this.at,
    required this.currentHour,
    required this.date,
    required this.riskScore,
    required this.riskLevel,
    required this.predictedViolationsThisHour,
    required this.dailyPrediction,
    required this.dailyBand,
    required this.dailySource,
    required this.confidence,
    required this.reasons,
    required this.adjustmentApplied,
    required this.adjustmentFactor,
    required this.allSignalsStale,
    required this.estimate,
    required this.hourly,
  });
  final String cameraId;
  final String? intersection;
  final double distanceM;
  final double? latitude;
  final double? longitude;
  final String at;
  final int currentHour; // Chicago hour
  final String date;
  final double riskScore; // current hour / 35
  final String riskLevel; // hourly band of the current hour
  final double predictedViolationsThisHour;
  final double dailyPrediction;
  final String dailyBand;
  final String dailySource;
  final double confidence;
  final List<String> reasons;
  // Live adjustment is EXPERIMENTAL and does NOT ship: the backend
  // always returns adjustment_applied=false, factor=1.0. The app must
  // NOT apply its own live adjustment on top of these values.
  final bool adjustmentApplied;
  final double adjustmentFactor;
  final bool allSignalsStale;
  final bool estimate; // always true
  final List<double> hourly; // float[24], sums to daily_prediction

  factory LiveRisk.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> adj =
        (json['live_adjustment'] as Map?)?.cast<String, dynamic>() ?? {};
    return LiveRisk(
      cameraId: (json['camera_id'] ?? '').toString(),
      intersection: json['intersection'] as String?,
      distanceM: (json['distance_m'] as num? ?? 0).toDouble(),
      latitude: (json['latitude'] as num?)?.toDouble(),
      longitude: (json['longitude'] as num?)?.toDouble(),
      at: (json['at'] ?? '').toString(),
      currentHour: (json['current_hour'] as num? ?? 0).toInt(),
      date: (json['date'] ?? '').toString(),
      riskScore: (json['risk_score'] as num).toDouble(),
      riskLevel: (json['risk_level'] ?? '').toString(),
      predictedViolationsThisHour:
          (json['predicted_violations_this_hour'] as num? ?? 0).toDouble(),
      dailyPrediction: (json['daily_prediction'] as num).toDouble(),
      dailyBand: (json['daily_band'] ?? '').toString(),
      dailySource: (json['daily_source'] ?? '').toString(),
      confidence: (json['confidence'] as num).toDouble(),
      reasons: ((json['reasons'] as List?) ?? [])
          .map((e) => e.toString())
          .toList(),
      adjustmentApplied: adj['adjustment_applied'] as bool? ?? false,
      adjustmentFactor: (adj['adjustment_factor'] as num? ?? 1).toDouble(),
      allSignalsStale: adj['all_signals_stale'] as bool? ?? false,
      estimate: json['estimate'] as bool? ?? true,
      hourly: ((json['hourly'] as List?) ?? [])
          .map((e) => (e as num).toDouble())
          .toList(),
    );
  }
}

class Health {
  Health({
    required this.status,
    required this.modelVersion,
    required this.modelFiles,
    required this.modelMetrics,
    required this.latestViolationDate,
  });
  final String status;
  final int modelVersion;
  final Map<String, dynamic> modelFiles;
  final Map<String, dynamic> modelMetrics;
  final String latestViolationDate;

  factory Health.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> freshness =
        (json['data_freshness'] as Map?)?.cast<String, dynamic>() ?? {};
    return Health(
      status: (json['status'] ?? '').toString(),
      modelVersion: (json['model_version'] as num? ?? 0).toInt(),
      modelFiles:
          (json['model_files'] as Map?)?.cast<String, dynamic>() ?? {},
      modelMetrics:
          (json['model_metrics'] as Map?)?.cast<String, dynamic>() ?? {},
      latestViolationDate:
          (freshness['latest_violation_date'] ?? '').toString(),
    );
  }
}

/// Latest published APK release, from GET /app/latest (see
/// lib/services/update_service.dart). All fields are null when the
/// backend could not reach GitHub (e.g. no token configured) — the
/// [reason] field then says why. The app decides whether an update is
/// available by comparing [build] with its own build number.
class LatestRelease {
  LatestRelease({
    required this.tag,
    required this.build,
    required this.name,
    required this.publishedAt,
    required this.downloadUrl,
    required this.notes,
    required this.reason,
  });
  final String? tag;
  final int? build;
  final String? name;
  final String? publishedAt;
  final String? downloadUrl;
  final String? notes;
  final String? reason;

  factory LatestRelease.fromJson(Map<String, dynamic> json) =>
      LatestRelease(
        tag: (json['tag'] as Object?)?.toString(),
        build: (json['build'] as num?)?.toInt(),
        name: (json['name'] as Object?)?.toString(),
        publishedAt: (json['published_at'] as Object?)?.toString(),
        downloadUrl: (json['download_url'] as Object?)?.toString(),
        notes: (json['notes'] as Object?)?.toString(),
        reason: (json['reason'] as Object?)?.toString(),
      );
}

class UserReport {
  UserReport({
    required this.id,
    required this.createdAt,
    required this.cameraId,
    required this.latitude,
    required this.longitude,
    required this.note,
  });
  final int id;
  final String createdAt;
  final String? cameraId;
  final double? latitude;
  final double? longitude;
  final String? note;

  factory UserReport.fromJson(Map<String, dynamic> json) => UserReport(
        id: (json['id'] as num).toInt(),
        createdAt: (json['created_at'] ?? '').toString(),
        cameraId: json['camera_id'] as String?,
        latitude: (json['latitude'] as num?)?.toDouble(),
        longitude: (json['longitude'] as num?)?.toDouble(),
        note: json['note'] as String?,
      );
}

class UserAlert {
  UserAlert({
    required this.id,
    required this.latitude,
    required this.longitude,
    required this.radiusM,
    required this.threshold,
    required this.thresholdValue,
    required this.name,
    required this.active,
  });
  final int id;
  final double latitude;
  final double longitude;
  final double radiusM;
  final String threshold; // LOW / MEDIUM / HIGH
  final double? thresholdValue;
  final String? name;
  final bool active;

  factory UserAlert.fromJson(Map<String, dynamic> json) => UserAlert(
        id: (json['id'] as num).toInt(),
        latitude: (json['latitude'] as num).toDouble(),
        longitude: (json['longitude'] as num).toDouble(),
        radiusM: (json['radius_m'] as num).toDouble(),
        threshold: (json['threshold'] ?? '').toString(),
        thresholdValue: (json['threshold_value'] as num?)?.toDouble(),
        name: json['name'] as String?,
        active: json['active'] as bool? ?? true,
      );
}

class UserSettings {
  UserSettings({
    required this.userId,
    required this.units,
    required this.alertRadiusM,
    required this.riskThreshold,
    required this.notifications,
    required this.raw,
  });
  final String userId;
  final String units; // metric | imperial
  final double alertRadiusM;
  final String riskThreshold; // LOW / MEDIUM / HIGH
  final Map<String, dynamic> notifications;
  final Map<String, dynamic> raw; // full merged settings map

  factory UserSettings.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> s =
        (json['settings'] as Map?)?.cast<String, dynamic>() ?? json;
    return UserSettings(
      userId: (json['user_id'] ?? '').toString(),
      units: (s['units'] ?? 'metric').toString(),
      alertRadiusM: (s['alert_radius_m'] as num? ?? 2000).toDouble(),
      riskThreshold: (s['risk_threshold'] ?? 'MEDIUM').toString(),
      notifications:
          (s['notifications'] as Map?)?.cast<String, dynamic>() ?? {},
      raw: s,
    );
  }
}

class UserFeedback {
  UserFeedback({required this.text, this.rating, this.category});
  final String text;
  final int? rating; // 1..5
  final String? category;

  Map<String, dynamic> toJson() => {
        'text': text,
        if (rating != null) 'rating': rating,
        if (category != null) 'category': category,
      };
}
