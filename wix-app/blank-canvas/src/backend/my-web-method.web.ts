/**
This file allows you to define backend functions that you can call from the front end of this app with type-safety.

Here's how you can call your web method from your frontend code:

import { multiply } from '<path-to-your-web-methods-directory>/my-web-method.web';

multiply(3, 4)
    .then(result => console.log(result));

To learn more, check out our documentation: https://wix.to/6LV6Oka.
*/

import { webMethod, Permissions } from '@wix/web-methods';

export const multiply = webMethod(
  Permissions.Anyone,
  (a: number, b: number) => {
    return a * b;
  },
);
