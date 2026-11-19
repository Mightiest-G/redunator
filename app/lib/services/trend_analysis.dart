// Risk-trend math for the Pro trend section and the saved-intersection
// spike flag.
//
// All inputs come from GET /violation_trends (DAILY granularity only):
// `days` is ascending by date and each day carries the OBSERVED daily
// violations (with a model prediction as fallback when observed is
// missing). No hourly claims are made here.
import '../api/models.dart';

enum TrendDirection { up, down, flat }

class TrendSummary {
  TrendSummary({
    required this.direction,
    required this.percentChange,
    required this.recentMean,
    required this.priorMean,
    required this.hasBaseline,
  });

  final TrendDirection direction;

  /// Percent change of the recent window mean vs the prior window
  /// mean. Null when there is no usable baseline (prior mean is 0).
  final double? percentChange;

  final double recentMean;
  final double priorMean;

  /// False when the prior window mean is 0 (or the prior window is
  /// empty): the direction is still reported, but no percent change
  /// can be computed from a zero baseline.
  final bool hasBaseline;
}

/// A change of less than this is noise, not a trend.
const double _kFlatThresholdPct = 5;

/// Compares the mean observed violations of the last [windowDays] days
/// against the [windowDays] days before them. Days are ascending by
/// date; windows are taken from the end of the list. When fewer than
/// [windowDays] days exist, the recent window is whatever is there and
/// the prior window is whatever remains before it.
TrendSummary analyzeTrend(List<TrendDay> days, int windowDays) {
  final values = _observedValues(days);
  if (values.isEmpty) {
    return TrendSummary(
      direction: TrendDirection.flat,
      percentChange: 0,
      recentMean: 0,
      priorMean: 0,
      hasBaseline: false,
    );
  }
  final recentCount = values.length < windowDays ? values.length : windowDays;
  final recent = values.sublist(values.length - recentCount);
  final priorPool = values.sublist(0, values.length - recentCount);
  final priorCount = priorPool.length < windowDays
      ? priorPool.length
      : windowDays;
  final prior = priorCount > 0
      ? priorPool.sublist(priorPool.length - priorCount)
      : <double>[];
  final recentMean = _mean(recent);
  final priorMean = _mean(prior);
  if (prior.isEmpty || priorMean == 0) {
    return TrendSummary(
      direction: recentMean == 0
          ? TrendDirection.flat
          : (recentMean > 0 ? TrendDirection.up : TrendDirection.flat),
      percentChange: null,
      recentMean: recentMean,
      priorMean: priorMean,
      hasBaseline: false,
    );
  }
  final pct = (recentMean - priorMean) / priorMean * 100;
  final direction = pct >= _kFlatThresholdPct
      ? TrendDirection.up
      : pct <= -_kFlatThresholdPct
      ? TrendDirection.down
      : TrendDirection.flat;
  return TrendSummary(
    direction: direction,
    percentChange: pct,
    recentMean: recentMean,
    priorMean: priorMean,
    hasBaseline: true,
  );
}

/// Mean observed daily violations over the last [windowDays] days of
/// [days] (ascending), or null when there is no usable data. Used for
/// the spike baseline.
double? meanObservedWindow(List<TrendDay> days, int windowDays) {
  final values = _observedValues(days);
  if (values.isEmpty) return null;
  final count = values.length < windowDays ? values.length : windowDays;
  return _mean(values.sublist(values.length - count));
}

/// The last [count] daily observed values (ascending), for the
/// sparkline. Missing days are simply absent — the list is not padded.
List<double> sparklineValues(List<TrendDay> days, int count) {
  final values = _observedValues(days);
  if (values.length <= count) return values;
  return values.sublist(values.length - count);
}

/// SPIKE RULE (Pro saved-intersection alerts): today counts as a spike
/// when the model's predicted daily violations for today are more than
/// 1.5x the mean OBSERVED daily violations over the last 7 days of
/// trend data. The mean comes from GET /violation_trends; today comes
/// from POST /predict_risk (or the batch). The two are different
/// quantities by design — observed past vs predicted today — so the
/// rule flags "today looks much worse than this camera's recent
/// normal". A zero or missing baseline never spikes: with no normal
/// to compare against, any small prediction would otherwise read as
/// an infinite increase.
bool isSpike(double todayDailyPrediction, double? mean7d) {
  if (mean7d == null || mean7d <= 0) return false;
  return todayDailyPrediction > 1.5 * mean7d;
}

List<double> _observedValues(List<TrendDay> days) => days
    .map((d) => d.observedViolations ?? d.predictedViolations)
    .whereType<double>()
    .toList();

double _mean(List<double> values) {
  if (values.isEmpty) return 0;
  var sum = 0.0;
  for (final v in values) {
    sum += v;
  }
  return sum / values.length;
}
