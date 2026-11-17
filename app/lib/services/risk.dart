// Risk bands, scores and marker colours.
//
// Sources: backend/config.py + Phase 1 hourly-bands work (see
// API_REFERENCE.md "Scales and bands"). These thresholds come from the
// backend/PDF spec — do NOT change them in the app.
//
// DAILY:   risk_score = clip(daily_prediction / 35, 0, 1)
//          LOW < 4, MEDIUM 4–<10, HIGH >= 10 predicted violations/day.
// HOURLY:  risk_score = hourly_prediction / 35 (same /35 scale — the
//          app must NOT invent a new hourly score scale; an open
//          decision on hourly scaling is pending, so the backend's
//          returned risk_score is used verbatim everywhere).
//          LOW < 0.1, MEDIUM 0.1–<0.5, HIGH >= 0.5 predicted/hour.
//
// MAP MARKER COLOURS (PDF thresholds, on the risk_score 0–1 scale):
//          Green  < 40%  (risk_score < 0.40)
//          Yellow 40–70% (0.40 <= risk_score <= 0.70)
//          Red    > 70%  (risk_score > 0.70)
import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:google_maps_flutter/google_maps_flutter.dart';

/// Daily band thresholds (predicted violations/day).
const double dailyLowMax = 4.0;
const double dailyHighMin = 10.0;

/// Hourly band thresholds (predicted violations/hour, Phase 1 fixed).
const double hourlyLowMax = 0.1;
const double hourlyHighMin = 0.5;

/// Risk-score scale denominator (backend: /35).
const double riskScoreScale = 35.0;

/// Marker colour cut-offs on the 0–1 risk_score scale.
const double markerYellowMin = 0.40;
const double markerRedMin = 0.70;

/// The label shown under every hourly value in the app.
const String hourlyEstimateLabel =
    'Hourly values are estimates from proxies — not observed hourly violations.';

String dailyBandFor(double dailyPrediction) {
  if (dailyPrediction < dailyLowMax) return 'LOW';
  if (dailyPrediction < dailyHighMin) return 'MEDIUM';
  return 'HIGH';
}

String hourlyBandFor(double hourlyPrediction) {
  if (hourlyPrediction < hourlyLowMax) return 'LOW';
  if (hourlyPrediction < hourlyHighMin) return 'MEDIUM';
  return 'HIGH';
}

/// Marker colour for a 0–1 risk score: green <40%, yellow 40–70%,
/// red >70%.
Color markerColorForScore(double riskScore) {
  if (riskScore > markerRedMin) return Colors.red;
  if (riskScore >= markerYellowMin) return Colors.amber.shade700;
  return Colors.green;
}

/// Hue used for google_maps_flutter BitmapDescriptor markers.
double markerHueForScore(double riskScore) {
  if (riskScore > markerRedMin) return BitmapDescriptor.hueRed;
  if (riskScore >= markerYellowMin) return BitmapDescriptor.hueYellow;
  return BitmapDescriptor.hueGreen;
}

Color bandColor(String band) {
  switch (band.toUpperCase()) {
    case 'HIGH':
      return Colors.red;
    case 'MEDIUM':
      return Colors.amber.shade700;
    default:
      return Colors.green;
  }
}

/// risk_score = clip(prediction / 35, 0, 1). The app uses the backend's
/// returned risk_score wherever one exists; this helper is only for
/// display fallbacks (e.g. typical weekday scores already shipped by
/// /intersections are used directly).
double riskScoreFor(double prediction) =>
    (prediction / riskScoreScale).clamp(0.0, 1.0);

String formatPercent(double riskScore) =>
    '${(riskScore * 100).round()}%';
