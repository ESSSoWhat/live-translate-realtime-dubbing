/// Supported languages for mic translation (aligned with desktop / backend).
class AppLanguage {
  const AppLanguage(this.code, this.name);

  final String code;
  final String name;
}

const List<AppLanguage> kSupportedLanguages = [
  AppLanguage('en', 'English'),
  AppLanguage('es', 'Spanish'),
  AppLanguage('ja', 'Japanese'),
  AppLanguage('ko', 'Korean'),
  AppLanguage('zh', 'Chinese (Mandarin)'),
  AppLanguage('id', 'Indonesian'),
  AppLanguage('th', 'Thai'),
  AppLanguage('ru', 'Russian'),
  AppLanguage('hi', 'Hindi'),
  AppLanguage('vi', 'Vietnamese'),
  AppLanguage('tl', 'Filipino (Tagalog)'),
  AppLanguage('kk', 'Kazakh'),
];

const List<AppLanguage> kSourceLanguages = [
  AppLanguage('auto', 'Auto-detect'),
  ...kSupportedLanguages,
];

String languageName(String code) {
  for (final lang in kSourceLanguages) {
    if (lang.code == code) return lang.name;
  }
  return code;
}
