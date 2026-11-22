---
title: Settings
---

# Settings

**Settings**, in the account menu, has eight tabs: Profile, Preferences, Personalization, Memory, API keys, Security, Notifications and App.

## Profile

![Profile tab](/img/guide/settings-profile.png)

You can change your name (the password is on the **Security** tab). You can check your role and join date, the remaining credits and the allowance for this month, and the next refill date. Credits are reset to the allowance on the first day of each month and do not carry over. The email address cannot be changed.

## Preferences

![Preferences tab](/img/guide/settings-preferences.png)

| Item | Description |
|---|---|
| Default model per screen | The model applied to new conversations on each screen, such as Chat, Reports, and Slides. It is saved to the account, so it applies the same way on every device. Chat, Reports and Slides also offer Super Agent and Super Agent (strict-local). Left unset, the administrator's default applies, which starts as Super Agent. |
| A request with personal data in it → What to do by default | Choose from asking every time, switching to strict-local, masking, and sending the original text (when the administrator allows it). [Privacy](../privacy) |
| A request with personal data in it → strict-local image model | The model that draws a picture in a conversation or document with internal material when it must stay strict-local and the picked image model is external. Shown only when there is more than one strict-local image model. Unset, `strict-local/z-image-turbo` is used, or the first in the list when that model is not served. |
| Response streaming | When it is off, the answer is shown all at once after it is complete. |
| Save memory automatically | Automatically saves information that stays valid, for each answer. |
| Hold Space to dictate | Hold `Space` in an empty composer and speak; on release the words are typed into the box, not sent. Shown only when speech transcription is connected, and off by default. |
| Token and credit display | Sets whether usage is shown at the bottom of the answer. |

Light or dark appearance (system, light, dark) and the language are switched with the buttons at the top right of the screen, not in this tab.

## Text size

KloudChat has no text-size setting of its own. To read larger text, zoom the browser: `Ctrl` + `+` on Windows (`Ctrl` + `-` to shrink, `Ctrl` + `0` to reset), `Cmd` + `+` on a Mac. At 200% the screen reflows and stays usable. On a phone, pinch to zoom.

## Personalization

![Personalization tab](/img/guide/settings-personalization.png)

- **Things worth knowing about me**: Describe your affiliation, your area of work, your level of understanding, and similar details. It applies **to conversations only** and is not delivered to reports or slide decks.
- **Answer style**: Describe the length, the tone, and the format. It applies to both conversations and documents. Write entries such as "no introduction", "always include an example", or "give English terms alongside".

The settings apply from the next conversation onward. If an agent or a project has instructions, those take precedence. You can open the tab directly with `Alt+Shift+P`, and when it is applied the processing steps of the answer show **Personalization applied**.

## Memory

See and edit what is remembered across conversations. See [Memory](memory.md) for details.

## Document look

Settings has no tab for the look of documents. Set the colours, fonts and table style of reports and slides in words in the **design brief** of a [project](projects.md#design-brief) or an agent, or attach a company form file. The look of a single document is changed with **Look** in the report or slides panel.

## API keys

![API keys tab](/img/guide/settings-keys.png)

Issue keys for use in external tools or scripts. You can issue up to 10 keys per account. The key value is shown only once at issue time, so copy it immediately. In the list you can check the issue date and the last use time. **Revoke** blocks a key immediately. The allowed model limits set on the account apply to keys in the same way. [Integrating the API and coding tools](../api)

## Security

![Security tab](/img/guide/settings-access.png)

- **Signed-in devices**: Shows the browser, the IP, and the last use time. The current device is marked, and individual sign-out and **Sign out all other devices** are provided. Calls made through an API key are not shown in this list.
- **Change password**: Enter the current password and a new password of at least 10 characters. Changing it signs out the sessions on your other devices.
- **Sign-in history**: Shows the last 100 sign-ins, failed sign-ins, password changes, and keys issued, together with the IP, the region, and the browser. Region information is shown only when the administrator has configured location data, and private networks are marked as "internal network".
- Two-factor authentication is not provided. Use a sufficiently long password and sign out the devices you do not recognize.

## Notifications

When a long job (a report, slides, a document, deep research, a video, studio output or a chain) finishes, you get a notification. You are asked once, the first time a long job starts; **Turn on for this device** switches it on at any time. Notifications arrive even with the tab closed. Each event can be switched on or off, and the devices that get notifications are listed and can be removed. A notification carries only the conversation title; conversations with internal material are notified without it. iPhone and iPad receive them only in the app added to the Home Screen (iOS 16.4 or later).

## App

**Install as an app** makes KloudChat open in its own window without browser tabs. In Safari, use “Add to Home Screen” in the Share menu (on a Mac, “Add to Dock” in the File menu).
