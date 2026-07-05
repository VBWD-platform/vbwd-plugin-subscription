# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Feature-declared access levels now revoke on user-cancel and on expiry.**
  Two gaps meant a plan's `access_levels` grant could outlive the subscription:
  (1) the user-facing cancel route (`POST /api/v1/user/subscriptions/<id>/cancel`)
  never published `subscription.cancelled`, so no lifecycle handler ran on user
  cancel — it now publishes via `publish_subscription_event` (matching the admin
  route), which also fixes S69 permission-sync + S73 group-sync not running on
  user cancel; and (2) `SubscriptionAccessLevelHandler` was not subscribed to
  `subscription.expired`, so scheduler-driven lapse never revoked. The handler's
  end-of-subscription logic (now `on_subscription_ended`, with
  `on_subscription_cancelled` kept as a back-compat alias) is subscribed to both
  `subscription.cancelled` and `subscription.expired`.

### Added
- **Automatic access-level switch from the plan Features field.** A tarif plan
  can now grant one or more `AccessLevel`s to the subscriber on payment by adding
  a single line to *Features (one per line)*:
  `access_levels: premium, vip`. On `subscription.activated` the named levels
  (looked up by slug) are assigned; on `subscription.cancelled` they are revoked
  **overlap-safe** — a level is retained while any other still-active plan of the
  user declares it. New `PlanFeatureAccessLevelService`
  (`services/plan_feature_access_level_service.py`) owns parse + grant + revoke;
  `SubscriptionAccessLevelHandler` delegates to it (alongside the existing
  `linked_plan_slug` grant) and commits its own session. Core is untouched — the
  feature reaches core only through the agnostic `UserAccessLevelService`.
  The named access levels must pre-exist as `vbwd_access_level` records.

## [v26.6] - 2026-06-26

### Added
- Initial tracked release tagged `v26.6`.
