/**
 * Test timing budget, shared by both Vitest projects.
 *
 * Testing Library's default wait (1000 ms for findBy* and waitFor) is too short for screens that
 * appear only after several mocked round-trips when the machine is loaded (parallel suites, CI).
 * A longer wait never makes a correct test slower: it only matters when the element is late.
 */
export const ASYNC_UTIL_TIMEOUT_MS = 5000;

/** Per-test limit for the unit and a11y projects; must leave room for several waits of ASYNC_UTIL_TIMEOUT_MS. */
export const UNIT_TEST_TIMEOUT_MS = 20_000;
