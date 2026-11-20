// ApkInstaller tests: the channel contract — 'ok' means the installer
// opened, anything else is shown to the user, and a platform failure
// never throws out of installApk.
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/services/apk_installer.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('redunator/install');
  late List<MethodCall> calls;

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  void mockHandler(Future<String?> Function(MethodCall call) handler) {
    calls = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      return handler(call);
    });
  }

  test('returns ok and passes the APK path when the installer opens', () async {
    mockHandler((_) async => 'ok');
    final result = await ApkInstaller().installApk('/files/redunator-apk-12.apk');
    expect(result, 'ok');
    expect(calls, hasLength(1));
    expect(calls.first.method, 'installApk');
    expect(calls.first.arguments, {'path': '/files/redunator-apk-12.apk'});
  });

  test('returns the native error message when the installer refuses', () async {
    mockHandler((_) async => 'The downloaded file is missing.');
    final result = await ApkInstaller().installApk('/files/missing.apk');
    expect(result, 'The downloaded file is missing.');
  });

  test('a platform exception becomes a friendly message, never a throw',
      () async {
    mockHandler((_) async => throw PlatformException(code: 'NO_PATH'));
    final result = await ApkInstaller().installApk('/files/x.apk');
    expect(result, isNot('ok'));
    expect(result, contains('installer'));
  });
}
