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
}
