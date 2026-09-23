import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app_update_service.dart';

final appUpdateServiceProvider = Provider<AppUpdateService>(
  (ref) => AppUpdateService(),
);
