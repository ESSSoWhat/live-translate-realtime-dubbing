import 'package:flutter_test/flutter_test.dart';
import 'package:live_translate_mobile/services/register_status.dart';

void main() {
  test('202 with needs_confirmation is success', () {
    final result = RegisterResult.fromResponse(
      statusCode: 202,
      body: {
        'needs_confirmation': true,
        'message': confirmEmailMessage,
        'email': 'a@b.c',
      },
    );
    expect(result.needsConfirmation, isTrue);
    expect(result.message, contains('Check your email to confirm your account'));
  });

  test('old 400 check-your-email is treated as success', () {
    final result = RegisterResult.fromResponse(
      statusCode: 400,
      body: {
        'detail': 'Please check your email to confirm your account, then sign in.',
      },
    );
    expect(result.needsConfirmation, isTrue);
  });

  test('201 with tokens is signed in', () {
    final result = RegisterResult.fromResponse(
      statusCode: 201,
      body: {'access_token': 'at', 'refresh_token': 'rt'},
    );
    expect(result.signedIn, isTrue);
  });

  test('504 and timeouts ask the user to check inbox', () {
    expect(
      RegisterResult.fromResponse(statusCode: 504, body: {'detail': 'gateway'})
          .isTimeout,
      isTrue,
    );
    final result = RegisterResult.fromResponse(
      statusCode: 0,
      body: {},
      timedOut: true,
    );
    expect(result.isTimeout, isTrue);
    expect(result.message, contains('Check your inbox before trying again'));
  });

  test('plain auth failure stays an error', () {
    final result = RegisterResult.fromResponse(
      statusCode: 400,
      body: {'detail': 'Authentication failed'},
    );
    expect(result.kind, 'error');
    expect(result.message, 'Authentication failed');
  });
}
