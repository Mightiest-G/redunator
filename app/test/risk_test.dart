// Risk-band / marker-colour tests (thresholds from backend/config.py
// and the Phase 1 hourly bands — see lib/services/risk.dart).
import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:flutter_test/flutter_test.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:redunator/services/risk.dart';

void main() {
  group('daily bands', () {
    test('LOW <4, MEDIUM 4–<10, HIGH >=10', () {
      expect(dailyBandFor(3.99), 'LOW');
      expect(dailyBandFor(4.0), 'MEDIUM');
      expect(dailyBandFor(9.99), 'MEDIUM');
      expect(dailyBandFor(10.0), 'HIGH');
      expect(dailyBandFor(53.0), 'HIGH');
    });
  });

  group('hourly bands (Phase 1 fixed)', () {
    test('LOW <0.1, MEDIUM 0.1–<0.5, HIGH >=0.5', () {
      expect(hourlyBandFor(0.099), 'LOW');
      expect(hourlyBandFor(0.1), 'MEDIUM');
      expect(hourlyBandFor(0.499), 'MEDIUM');
      expect(hourlyBandFor(0.5), 'HIGH');
      expect(hourlyBandFor(4.8194), 'HIGH');
    });
  });

  group('marker colours (PDF thresholds on risk_score)', () {
    test('green <40%, yellow 40–70%, red >70%', () {
      expect(markerColorForScore(0.39), Colors.green);
      expect(markerColorForScore(0.40), Colors.amber.shade700);
      expect(markerColorForScore(0.70), Colors.amber.shade700);
      expect(markerColorForScore(0.71), Colors.red);
      expect(markerColorForScore(1.0), Colors.red);
    });

    test('hue matches colour', () {
      expect(markerHueForScore(0.2), BitmapDescriptor.hueGreen);
      expect(markerHueForScore(0.5), BitmapDescriptor.hueYellow);
      expect(markerHueForScore(0.9), BitmapDescriptor.hueRed);
    });
  });

  test('riskScoreFor clips pred/35 to 0–1', () {
    expect(riskScoreFor(0), 0.0);
    expect(riskScoreFor(17.5), 0.5);
    expect(riskScoreFor(35), 1.0);
    expect(riskScoreFor(53.0), 1.0);
    expect(riskScoreFor(-5), 0.0);
  });
}
