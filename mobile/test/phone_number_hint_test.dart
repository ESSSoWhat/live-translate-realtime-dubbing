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

  test('requestWhileListening starts the SMS listener before the request', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel =
        MethodChannel('app.livetranslate.live_translate_mobile/phone_number');
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    final events = <String>[];
    messenger.setMockMethodCallHandler(channel, (call) async {
      if (call.method == 'listenForCode') {
        events.add('listen');
        return '123456';
      }
      if (call.method == 'cancelCode') {
        events.add('cancel');
        return null;
      }
      return null;
    });
    try {
      final pending = await PhoneNumberHint.requestWhileListening<String>(
        isMounted: () => true,
        request: () async {
          events.add('request');
          return 'sent';
        },
      );
      expect(events, ['listen', 'request']);
      expect(pending.result, 'sent');
      expect(await pending.code, '123456');
    } finally {
      messenger.setMockMethodCallHandler(channel, null);
      debugDefaultTargetPlatformOverride = null;
    }
  });

  test('requestWhileListening cancels the listener if the screen is gone', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel =
        MethodChannel('app.livetranslate.live_translate_mobile/phone_number');
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    final events = <String>[];
    messenger.setMockMethodCallHandler(channel, (call) async {
      if (call.method == 'listenForCode') {
        events.add('listen');
        return '123456';
      }
      if (call.method == 'cancelCode') {
        events.add('cancel');
        return null;
      }
      return null;
    });
    try {
      var mounted = true;
      final pending = await PhoneNumberHint.requestWhileListening<String>(
        isMounted: () => mounted,
        request: () async {
          mounted = false;
          return 'sent';
        },
      );
      expect(pending.result, isNull);
      expect(events, containsAll(['listen', 'cancel']));
    } finally {
      messenger.setMockMethodCallHandler(channel, null);
      debugDefaultTargetPlatformOverride = null;
    }
  });
}
