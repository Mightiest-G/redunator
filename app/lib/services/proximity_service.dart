// Proximity alerts: watches the device location (geolocator) and
// plays an audio alert (audioplayers) when the user comes within the
// configured distance of a camera whose risk_score is above the
// configured threshold.
//
// PLATFORM LIMITS (documented honestly — do NOT claim more):
// - Foreground: fully supported. The watcher runs while the app is
//   open (any tab); that is the mode this service ships.
// - Background: this service does NOT run in the background. Pro
//   background alerts are a separate mechanism — a WorkManager
//   periodic task (see background_alerts.dart) that checks about
//   every 15 minutes while the app is closed. Android may defer a
//   run (Doze, battery saver), and it needs location "Allow all the
//   time". When the app is backgrounded/killed and the user is NOT
//   Pro (or background alerts are off), alerts pause until it is
//   reopened. The Settings screen says so in plain language.
// - A per-camera cooldown (default 5 min) prevents repeat alerts
//   for the same camera while the user stays nearby.
// - Location permission is requested on first use; if denied, the
//   watcher stays off and the Map screen says so.
import 'dart:async';

import 'package:audioplayers/audioplayers.dart';
import 'package:geolocator/geolocator.dart';

import '../api/models.dart';

typedef AlertCallback = void Function(Intersection camera, double distanceM);

class ProximityService {
  ProximityService({
    this._player,
    this.currentPositionOverride,
    this.positionStreamOverride,
  });

  // Created lazily on first play: constructing an AudioPlayer touches
  // platform channels, which do not exist in plain unit tests (and
  // are not needed until an alert actually sounds).
  AudioPlayer? _player;
  AudioPlayer get _audio => _player ??= AudioPlayer();

  final Future<Position> Function()? currentPositionOverride;
  final Stream<Position> Function()? positionStreamOverride;

  StreamSubscription<Position>? _subscription;
  bool _running = false;
  bool get running => _running;

  /// Last alert time per camera_id (cooldown bookkeeping).
  final Map<String, DateTime> _lastAlertAt = {};

  static const Duration alertCooldown = Duration(minutes: 5);

  /// Checks location permission and requests it if needed.
  /// Returns true only when the app may read the location.
  Future<bool> ensurePermission() async {
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.deniedForever) return false;
    return permission == LocationPermission.always ||
        permission == LocationPermission.whileInUse;
  }

  Future<Position?> currentPosition() async {
    final override = currentPositionOverride;
    if (override != null) return override();
    if (!await ensurePermission()) return null;
    return Geolocator.getCurrentPosition();
  }

  /// Starts the foreground watcher. Safe to call repeatedly; a running
  /// watcher is restarted with the new camera list / thresholds.
  Future<void> start({
    required List<Intersection> cameras,
    required Map<String, Prediction> predictions,
    required double alertDistanceM,
    required double riskThresholdScore,
    required AlertCallback onAlert,
  }) async {
    await stop();
    if (!await ensurePermission()) return;
    _running = true;
    final stream = positionStreamOverride?.call() ??
        Geolocator.getPositionStream(
          locationSettings: const LocationSettings(
            accuracy: LocationAccuracy.high,
            distanceFilter: 25,
          ),
        );
    _subscription = stream.listen((pos) {
      _checkPosition(
        pos,
        cameras: cameras,
        predictions: predictions,
        alertDistanceM: alertDistanceM,
        riskThresholdScore: riskThresholdScore,
        onAlert: onAlert,
      );
    });
  }

  Future<void> stop() async {
    await _subscription?.cancel();
    _subscription = null;
    _running = false;
  }

  /// Pure-ish check used by the watcher and by tests: given a position,
  /// return the nearest camera that should trigger an alert, or null.
  /// A camera triggers when: it has coordinates, its risk_score (from
  /// the batch prediction if present, else its typical weekday score)
  /// is above [riskThresholdScore], it is within [alertDistanceM],
  /// and it has not alerted within [alertCooldown].
  Intersection? checkPosition(
    Position position, {
    required List<Intersection> cameras,
    required Map<String, Prediction> predictions,
    required double alertDistanceM,
    required double riskThresholdScore,
    DateTime? now,
  }) {
    final at = now ?? DateTime.now();
    Intersection? best;
    var bestDist = double.infinity;
    for (final cam in cameras) {
      if (cam.latitude == null || cam.longitude == null) continue;
      final pred = predictions[cam.cameraId];
      final score = pred?.riskScore ?? cam.typicalWeekdayScore;
      if (score <= riskThresholdScore) continue;
      final dist = Geolocator.distanceBetween(
        position.latitude,
        position.longitude,
        cam.latitude!,
        cam.longitude!,
      );
      if (dist > alertDistanceM || dist >= bestDist) continue;
      final last = _lastAlertAt[cam.cameraId];
      if (last != null && at.difference(last) < alertCooldown) continue;
      best = cam;
      bestDist = dist;
    }
    return best;
  }

  void _checkPosition(
    Position pos, {
    required List<Intersection> cameras,
    required Map<String, Prediction> predictions,
    required double alertDistanceM,
    required double riskThresholdScore,
    required AlertCallback onAlert,
  }) {
    final cam = checkPosition(
      pos,
      cameras: cameras,
      predictions: predictions,
      alertDistanceM: alertDistanceM,
      riskThresholdScore: riskThresholdScore,
    );
    if (cam == null) return;
    _lastAlertAt[cam.cameraId] = DateTime.now();
    final dist = Geolocator.distanceBetween(
      pos.latitude,
      pos.longitude,
      cam.latitude!,
      cam.longitude!,
    );
    unawaited(_playAlert());
    onAlert(cam, dist);
  }

  Future<void> _playAlert() async {
    try {
      await _audio.stop();
      await _audio.play(AssetSource('alert.wav'));
    } catch (_) {
      // Audio must never crash the app; the visual alert still shows.
    }
  }

  /// Manual test: plays the alert sound once.
  Future<void> testSound() async => _playAlert();

  Future<void> dispose() async {
    await stop();
    await _player?.dispose();
  }
}
