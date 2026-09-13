---
title: Administrator guide
---

# Administrator guide

When you sign in with an administrator account, a **Manage** item appears in the account menu. The first account to sign up holds administrator rights.

| Screen | What it does |
|---|---|
| Users · Credits | Approve and reject sign-ups, suspend, edit details, monthly credits, allowed models, key management |
| Usage | Usage by period, by model, and by user, CSV, storage space |
| Security · Audit | The **Policy** tab (personal data protection, safe model, blocked topics, idle sign-out, retention period) and the **Audit log** tab |
| System | The **Proxy · Routing · Features · Shared templates · Branding · Mail · Sign-up** tabs |

## Order of initial setup

1. **System → Proxy**: Enter the model gateway address and master key, then check the connection. Until this is set, no models are available to use.
2. **System → Features**: Enable the features you will use and test the connection to each feature integration address (web search, document import, code execution, deep research, speech transcription, material search).
3. **System → Sign-up**: Set the sign-up method and the allowed email domains, and enable email verification if you have a mail server.
4. **System → Mail**: Set up SMTP if you need password reset and verification mail.
5. **System → Branding**: Set the service name, logo, and contact address.
6. **Security · Audit → Policy**: Designate the strict-local safe model and decide how personal data is protected.
7. **System → Routing**: Decide whether to use Auto mode.
8. **Users · Credits**: Process the accounts waiting for approval.

For the details of each item, see [Users and credits](users.md), [Policy and routing](policy.md), and [System settings](system.md).
