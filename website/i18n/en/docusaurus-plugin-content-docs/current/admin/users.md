---
title: Users and credits
---

# Users and credits

This is the **Users · Credits** screen in the Manage menu. The top summarises the total number of users, the number waiting for approval, this month's usage and allocation, and the next refill date. The bottom offers tabs by status (All, Waiting for approval, Active, Suspended) and a search by name and email.

## Approving sign-ups

If the sign-up method is **Use after approval**, new accounts build up as waiting for approval and the account menu shows "N waiting for approval". Press **Approve** on a row to approve the account immediately with the default plan credits, and the model gateway user and key are created at that point. Handle accounts you will not approve with **Reject**. An account that has not completed email verification shows a **Mail not verified** badge, and approving it counts the verification as complete. Selecting several at once is not supported, so you have to approve each account individually.

## Management items per user

| Button | What it does |
|---|---|
| Credits | Change the monthly limit. The spare amount is reflected in the gateway budget as well |
| Model restriction | The models this account can use. Leave it empty for all of them. It applies to API keys too |
| Edit details | Name and email, **Reset password** (randomly generated, saving signs out every session) |
| Key management | Reissue the gateway key (the existing key is discarded at once) or replace it, and list and delete the API keys the user created |
| Suspend / Unsuspend | Reversible, and a record is kept |
| Delete | Deletes conversations, projects, results, memory, credit records, and the gateway key. You choose whether to delete the uploaded files and the generated image and video files from disk |

## Credit policy

The credits of an approved account reset to the allocation on the 1st of each month and do not carry over. In an environment that runs only free models, credits are not consumed, and the limit applies in practice only when external models or the image and video features are enabled.

## Cleaning up accounts

Delete accounts created for verification or load testing once you are done with them. If you do not delete them, they are counted in the usage totals as well.
