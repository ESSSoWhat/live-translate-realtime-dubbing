import { app } from "@wix/astro/builders";
import { dashboardpageLiveTranslate } from "./extensions/dashboard/pages/live-translate/extensions";

export default app().use(dashboardpageLiveTranslate);
