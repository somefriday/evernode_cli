//! When the current voting round ends and where the elections are: a pure function of params 15, 34 and 36
//! and the clock.
//!
//! A round is the time one validator set is current. It ends when the config contract moves the elected next
//! set (p36) into p34 at p36's start (`run_ticktock`), not at p34's planned end: an election that completes
//! late starts the next set late, and one that cannot complete leaves the current set in place. The elector
//! is never read: mainnet's FunC elector and the test network's Solidity one store their state differently,
//! and either can be replaced by a vote.

use crate::config::ValidatorSet;

/// Seconds before a round ends in which nothing is registered or voted: a vote signed with this set's key
/// that lands under the next set fails its signature check.
pub const BOUNDARY: u32 = 20;

/// What p15 says of a round: `validators_elected_for elections_start_before elections_end_before` (its last
/// field, `stake_held_for`, is not needed here).
#[derive(Debug, Clone, Copy)]
pub struct ElectionTiming {
    pub elected_for: u32,
    pub start_before: u32,
    pub end_before: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    /// the next set is elected (p36): the round ends when it starts
    NextElected,
    /// the elections for the next set open at `opens`
    BeforeElections,
    /// the elections are open until `closes`
    ElectionsOpen,
    /// the elections closed and the new set is not in p36 yet
    WaitingForSet,
    /// p34's planned end passed with no new set elected: the round goes on until one is
    Overdue,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Timeline {
    pub now: u32,
    /// when the current round ends: p36's start once the next set is elected, otherwise p34's planned end
    pub round_end: u32,
    /// `round_end` is p36's start, not a plan
    pub exact: bool,
    /// the elections for the next set, from p34's planned end and p15
    pub opens: u32,
    pub closes: u32,
    pub phase: Phase,
    /// a round's planned length (p15 `validators_elected_for`)
    pub round_length: u32,
}

impl Timeline {
    pub fn new(current: &ValidatorSet, next: Option<&ValidatorSet>, timing: &ElectionTiming, now: u32) -> Self {
        Self::from_times(current.utime_until, next.map(|n| n.utime_since), timing, now)
    }

    pub(crate) fn from_times(planned_end: u32, next_start: Option<u32>, timing: &ElectionTiming, now: u32) -> Self {
        let opens = planned_end.saturating_sub(timing.start_before);
        let closes = planned_end.saturating_sub(timing.end_before);
        let (round_end, exact, phase) = match next_start {
            Some(start) => (start, true, Phase::NextElected),
            None if now < opens => (planned_end, false, Phase::BeforeElections),
            None if now < closes => (planned_end, false, Phase::ElectionsOpen),
            None if now < planned_end => (planned_end, false, Phase::WaitingForSet),
            None => (planned_end, false, Phase::Overdue),
        };
        Self { now, round_end, exact, opens, closes, phase, round_length: timing.elected_for }
    }

    /// When the next round starts, as far as anyone can know now: p36's start, else p34's planned end. A
    /// switch that is due happens in the next block or two; an overdue round is looked at again later.
    pub fn next_round_start(&self) -> u32 {
        if self.round_end > self.now {
            self.round_end
        } else if self.phase == Phase::NextElected {
            self.now + 60
        } else {
            self.now + self.overdue_recheck()
        }
    }

    /// While a round is overdue (no next set elected), how often to look again: a tenth of a round, from a
    /// minute to ten. Mainnet looks every ten minutes; a ten-minute test round, every minute, so that a set
    /// elected late is voted in from its start.
    fn overdue_recheck(&self) -> u32 {
        (self.round_length / 10).clamp(60, 600)
    }

    /// Is a switch to the next set due about now? From [`BOUNDARY`] seconds before the round's end until 60 s
    /// after it. A round that outlives its planned end with no next set is simply the current round again.
    pub fn near_switch(&self) -> bool {
        self.now + BOUNDARY >= self.round_end && self.now < self.round_end + 60
    }

    /// Have the elections for the next set closed? A proposal registered now has little time left to win
    /// this round, and a round it does not win uses one of its allowed losses.
    pub fn elections_closed(&self) -> bool {
        matches!(self.phase, Phase::NextElected | Phase::WaitingForSet)
    }

    /// Two lines for `status`: when the round ends, and where the elections are.
    pub fn describe(&self) -> (String, String) {
        let round = match self.phase {
            Phase::Overdue => format!(
                "overdue since {} ({} ago): no new set is elected yet, so the round goes on until one is",
                utc(self.round_end),
                span(self.now - self.round_end)
            ),
            _ if self.exact => format!("ends {} ({}), when the elected next set starts", utc(self.round_end), until(self.round_end, self.now)),
            _ => format!(
                "ends about {} ({}), p34's planned end; exact once the next set is elected",
                utc(self.round_end),
                until(self.round_end, self.now)
            ),
        };
        let elections = match self.phase {
            Phase::NextElected => "closed; the next set is elected".to_string(),
            Phase::BeforeElections => {
                format!("open {} ({}), close {}", utc(self.opens), until(self.opens, self.now), utc(self.closes))
            }
            Phase::ElectionsOpen => format!("open now, close {} ({})", utc(self.closes), until(self.closes, self.now)),
            Phase::WaitingForSet => format!("closed {}; waiting for the new set", utc(self.closes)),
            Phase::Overdue => format!("closed {}; no new set was elected", utc(self.closes)),
        };
        (round, elections)
    }
}

/// A unix time as `2026-11-09 08:23:12Z`.
pub fn utc(t: u32) -> String {
    // days since 1970-01-01 to a civil date (H. Hinnant's algorithm)
    let days = (t / 86_400) as i64 + 719_468;
    let era = days.div_euclid(146_097);
    let doe = days - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    let s = t % 86_400;
    format!("{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}Z", s / 3600, s / 60 % 60, s % 60)
}

/// A duration as `4h05m`, `12m30s` or `40s`.
pub fn span(seconds: u32) -> String {
    match seconds {
        s if s >= 3600 => format!("{}h{:02}m", s / 3600, s / 60 % 60),
        s if s >= 60 => format!("{}m{:02}s", s / 60, s % 60),
        s => format!("{s}s"),
    }
}

/// When a set ends at `t`, from `now`: `ends in 4m05s` or `ended 2m10s ago`.
pub fn ends(t: u32, now: u32) -> String {
    if t >= now { format!("ends in {}", span(t - now)) } else { format!("ended {} ago", span(now - t)) }
}

fn until(t: u32, now: u32) -> String {
    if t >= now { format!("in {}", span(t - now)) } else { format!("{} ago", span(now - t)) }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Mainnet p15, read 2026-11-05.
    const MAINNET: ElectionTiming = ElectionTiming { elected_for: 65_536, start_before: 32_768, end_before: 8_192 };
    /// The local test network's p15.
    const LOCAL: ElectionTiming = ElectionTiming { elected_for: 300, start_before: 200, end_before: 80 };
    /// Mainnet's switch of 2026-11-06 14:11:24Z.
    const END: u32 = 1_793_974_284;

    #[test]
    fn mainnet_round_before_during_and_after_the_elections() {
        let at = |now| Timeline::from_times(END, None, &MAINNET, now);
        let before = at(END - 40_000);
        assert_eq!((before.phase, before.opens, before.closes), (Phase::BeforeElections, END - 32_768, END - 8_192));
        assert_eq!(utc(before.opens), "2026-11-06 05:05:16Z");
        assert_eq!(utc(before.closes), "2026-11-06 11:54:52Z");
        assert_eq!(at(END - 20_000).phase, Phase::ElectionsOpen);
        assert_eq!(at(END - 100).phase, Phase::WaitingForSet);
        // without p36 the end is the plan; the next round starts there
        assert!(!before.exact);
        assert_eq!(before.next_round_start(), END);
        // the next set is elected at the close: the end is exact
        let elected = Timeline::from_times(END, Some(END), &MAINNET, END - 8_000);
        assert_eq!((elected.phase, elected.exact, elected.next_round_start()), (Phase::NextElected, true, END));
        assert!(elected.elections_closed() && !before.elections_closed());
    }

    #[test]
    fn a_stalled_election_leaves_the_round_overdue() {
        // local network, 2026-11-05: elections could not complete and the round went on for 33 hours
        let t = Timeline::from_times(END, None, &LOCAL, END + 33 * 3600);
        assert_eq!(t.phase, Phase::Overdue);
        assert!(!t.near_switch(), "votes go on in an overdue round");
        assert_eq!(t.next_round_start(), t.now + 60, "a 300 s round is looked at every minute");
        let mainnet = Timeline::from_times(END, None, &MAINNET, END + 3600);
        assert_eq!(mainnet.next_round_start(), mainnet.now + 600, "an 18-hour round, every ten minutes");
        assert!(t.describe().0.starts_with("overdue since"));
    }

    #[test]
    fn a_late_election_moves_the_end_to_the_next_sets_start() {
        // the new set starts 20 s after p36 appears (max(now + end_before - 60, planned start))
        let now = END + 500;
        let t = Timeline::from_times(END, Some(now + 20), &LOCAL, now);
        assert_eq!((t.round_end, t.exact), (now + 20, true));
        assert!(t.near_switch(), "no votes in the last 20 s of a round");
        assert_eq!(t.next_round_start(), now + 20);
        // p36's start has passed, but the switch is not in a block yet: look again in a minute
        let due = Timeline::from_times(END, Some(now - 5), &LOCAL, now);
        assert_eq!(due.next_round_start(), now + 60);
    }

    #[test]
    fn the_boundary_is_twenty_seconds_before_the_switch_and_a_minute_after() {
        let at = |now| Timeline::from_times(END, Some(END), &LOCAL, now).near_switch();
        assert!(!at(END - BOUNDARY - 1));
        assert!(at(END - BOUNDARY));
        assert!(at(END + 59));
        assert!(!at(END + 60));
    }

    #[test]
    fn formats() {
        assert_eq!(utc(0), "1970-01-01 00:00:00Z");
        assert_eq!(utc(951_782_400), "2000-02-29 00:00:00Z");
        assert_eq!((span(40), span(750), span(14_700)), ("40s".into(), "12m30s".into(), "4h05m".into()));
    }
}
