/// Conditional export: stub on web, real implementation on mobile/desktop (dart.library.io).
library;

export 'package:live_translate_mobile/services/qonversion_models.dart';
export 'package:live_translate_mobile/services/qonversion_service_stub.dart'
    if (dart.library.io) 'package:live_translate_mobile/services/qonversion_service_io.dart';
