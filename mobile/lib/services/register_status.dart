/// Classify /auth/register responses without talking to a backend.
const String confirmEmailMessage =
    'Check your email to confirm your account, then sign in.';
const String signupTimeoutMessage =
    'This is taking longer than usual. Check your inbox before trying again.';

bool isConfirmationMessage(String? text) {
  final lowered = (text ?? '').toLowerCase();
  return lowered.contains('check your email') && lowered.contains('confirm');
}

/// Result of a sign-up attempt.
class RegisterResult {
  const RegisterResult._(this.kind, this.message, this.payload);

  final String kind; // signedIn | needsConfirmation | timeout | error
  final String message;
  final Map<String, dynamic> payload;

  bool get needsConfirmation => kind == 'needsConfirmation';
  bool get signedIn => kind == 'signedIn';
  bool get isTimeout => kind == 'timeout';

  factory RegisterResult.fromResponse({
    required int statusCode,
    Map<String, dynamic>? body,
    bool timedOut = false,
  }) {
    final payload = body ?? <String, dynamic>{};
    final detail = payload['detail'];
    String detailText;
    if (detail is List) {
      detailText = detail
          .map((item) => item is Map ? (item['msg'] ?? item).toString() : item.toString())
          .join(' ');
    } else if (detail == null) {
      detailText = '';
    } else {
      detailText = detail.toString();
    }
    final message = (payload['message'] as String?)?.isNotEmpty == true
        ? payload['message'] as String
        : detailText;

    if (timedOut || statusCode == 504) {
      return RegisterResult._('timeout', signupTimeoutMessage, payload);
    }
    if (payload['needs_confirmation'] == true || isConfirmationMessage(message)) {
      final text = message.isNotEmpty ? message : confirmEmailMessage;
      return RegisterResult._('needsConfirmation', text, {
        ...payload,
        'needs_confirmation': true,
        'message': text,
      });
    }
    if ((statusCode == 200 || statusCode == 201) &&
        (payload['access_token'] != null || payload['refresh_token'] != null)) {
      return RegisterResult._('signedIn', '', payload);
    }
    if (statusCode == 200 || statusCode == 201) {
      return RegisterResult._('needsConfirmation', confirmEmailMessage, {
        'needs_confirmation': true,
        'message': confirmEmailMessage,
      });
    }
    return RegisterResult._(
      'error',
      message.isNotEmpty ? message : 'HTTP $statusCode',
      payload,
    );
  }
}
