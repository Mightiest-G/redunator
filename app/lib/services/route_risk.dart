// Safest-time-to-leave math for the Pro Route tab.
//
// Pure: sums each near-route camera's HOURLY predicted violations per
// hour of day and picks the hour with the lowest total. The hourly
// values are ESTIMATES (see risk.dart) — the "safest hour" is a
// comparison of estimates, not observed hourly violations, and the UI
// says so.
import '../api/models.dart';

class SafestDeparture {
  SafestDeparture({
    required this.hour,
    required this.nowTotal,
    required this.safestTotal,
    required this.cameraCount,
  });

  /// Safest departure hour, 0–23.
  final int hour;

  /// Summed predicted violations/hour across the route's cameras at
  /// [currentHour] (the "now" the user is comparing against).
  final double nowTotal;

  /// Summed predicted violations/hour at [hour].
  final double safestTotal;

  /// Cameras that contributed hourly data (cameras without a
  /// prediction are skipped, not treated as zero).
  final int cameraCount;
}

/// Returns null when no camera has hourly data (nothing to compare).
/// Ties go to the earliest hour.
SafestDeparture? safestDepartureHour({
  required Map<String, Prediction> predictions,
  required List<String> cameraIds,
  required int currentHour,
}) {
  final totals = List<double>.filled(24, 0);
  var used = 0;
  for (final id in cameraIds) {
    final p = predictions[id];
    if (p == null || p.hourly.isEmpty) continue;
    used++;
    for (final e in p.hourly) {
      if (e.hour >= 0 && e.hour < 24) {
        totals[e.hour] += e.predictedViolations;
      }
    }
  }
  if (used == 0) return null;
  var bestHour = 0;
  for (var h = 1; h < 24; h++) {
    if (totals[h] < totals[bestHour]) bestHour = h;
  }
  return SafestDeparture(
    hour: bestHour,
    nowTotal: totals[currentHour.clamp(0, 23)],
    safestTotal: totals[bestHour],
    cameraCount: used,
  );
}
