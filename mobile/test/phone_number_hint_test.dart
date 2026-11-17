import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_translate_mobile/services/phone_number_hint.dart';

void main() {
  test('toE164 normalises picker numbers', () {
    expect(PhoneNumberHint.toE164('+61 412-345 678'), '+61412345678');
    expect(PhoneNumberHint.toE164('(+1) 415.555.0123'), '+14155550123');
    expect(PhoneNumberHint.toE164('0061412345678'), '+61412345678');
    expect(PhoneNumberHint.toE164('0412345678'), isNull);
    expect(PhoneNumberHint.toE164('+61123'), isNull);
  });

  test('channel calls fall back to null without the Android host', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    expect(await PhoneNumberHint.requestPhoneNumber(), isNull);
    expect(await PhoneNumberHint.listenForCode(), isNull);
  });

  test('cancelCodeListener tolerates a non-string host reply', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel =
        MethodChannel('app.livetranslate.live_translate_mobile/phone_number');
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    messenger.setMockMethodCallHandler(channel, (call) async {
      // Older Android hosts acknowledged cancelCode with `true`.
      return call.method == 'cancelCode' ? true : null;
    });
    try {
      await PhoneNumberHint.cancelCodeListener();
      expect(await PhoneNumberHint.requestPhoneNumber(), isNull);
    } finally {
      messenger.setMockMethodCallHandler(channel, null);
      debugDefaultTargetPlatformOverride = null;
    }
  });
}
