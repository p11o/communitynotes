from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import constants as c, scoring_rules, tag_filter
from .scoring_rules import RuleID

import numpy as np
import pandas as pd


_maxHistoricalValidRatings = 5


def is_crh(scoredNotes, minRatingsNeeded, crhThreshold) -> pd.Series:
  return (scoredNotes[c.numRatingsKey] >= minRatingsNeeded) & (
    scoredNotes[c.internalNoteInterceptKey] >= crhThreshold
  )


def is_crnh(
  scoredNotes, minRatingsNeeded, crnhThresholdIntercept, crnhThresholdNoteFactorMultiplier
) -> pd.Series:
  return (scoredNotes[c.numRatingsKey] >= minRatingsNeeded) & (
    scoredNotes[c.internalNoteInterceptKey]
    <= crnhThresholdIntercept
    + crnhThresholdNoteFactorMultiplier * np.abs(scoredNotes[c.internalNoteFactor1Key])
  )


def get_ratings_before_note_status_and_public_tsv(
  ratings: pd.DataFrame,
  noteStatusHistory: pd.DataFrame,
  logging: bool = True,
  doTypeCheck: bool = True,
) -> pd.DataFrame:
  """Determine which ratings are made before note's most recent non-NMR status,
  and before we could've released any information in the public TSV (48 hours after note creation).

  For old notes (created pre-tombstones launch May 19, 2022), take first 5 ratings.

  Args:
      ratings (pd.DataFrame)
      noteStatusHistory (pd.DataFrame)
      logging (bool, optional). Defaults to True.
      doTypeCheck (bool): do asserts to check types.
  Returns:
      pd.DataFrame combinedRatingsBeforeStatus ratings that were created early enough to be valid ratings
  """
  right_suffix = "_note"
  ratingsWithNoteLabelInfo = ratings[
    [c.raterParticipantIdKey, c.noteIdKey, c.helpfulNumKey, c.createdAtMillisKey]
  ].merge(
    noteStatusHistory[
      [c.noteIdKey, c.createdAtMillisKey, c.timestampMillisOfNoteMostRecentNonNMRLabelKey]
    ],
    on=c.noteIdKey,
    how="left",
    suffixes=("", right_suffix),
  )
  ratingsWithNoteLabelInfo[
    [c.createdAtMillisKey + right_suffix, c.timestampMillisOfNoteMostRecentNonNMRLabelKey]
  ] *= 1.0

  if doTypeCheck:
    ratingsWithNoteLabelInfoTypes = c.ratingTSVTypeMapping
    ratingsWithNoteLabelInfoTypes[
      c.createdAtMillisKey + "_note"
    ] = np.float64  # float because nullable after merge.
    ratingsWithNoteLabelInfoTypes[
      c.timestampMillisOfNoteMostRecentNonNMRLabelKey
    ] = np.float64  # float because nullable.
    ratingsWithNoteLabelInfoTypes[c.helpfulNumKey] = np.float64

    assert len(ratingsWithNoteLabelInfo) == len(ratings)
    mismatches = [
      (c, dtype, ratingsWithNoteLabelInfoTypes[c])
      for c, dtype in zip(ratingsWithNoteLabelInfo, ratingsWithNoteLabelInfo.dtypes)
      if dtype != ratingsWithNoteLabelInfoTypes[c]
    ]
    assert not len(mismatches), f"Mismatch columns: {mismatches}"

  ratingsWithNoteLabelInfo[c.ratingCreatedBeforeMostRecentNMRLabelKey] = (
    pd.isna(ratingsWithNoteLabelInfo[c.timestampMillisOfNoteMostRecentNonNMRLabelKey])
  ) | (
    ratingsWithNoteLabelInfo[c.createdAtMillisKey]
    < ratingsWithNoteLabelInfo[c.timestampMillisOfNoteMostRecentNonNMRLabelKey]
  )

  ratingsWithNoteLabelInfo[c.ratingCreatedBeforePublicTSVReleasedKey] = (
    ratingsWithNoteLabelInfo[c.createdAtMillisKey]
    - ratingsWithNoteLabelInfo[c.createdAtMillisKey + "_note"]
    < c.publicTSVTimeDelay
  )

  noteCreatedBeforeNoteStatusHistory = (
    ratingsWithNoteLabelInfo[c.createdAtMillisKey + "_note"] < c.deletedNoteTombstonesLaunchTime
  )

  first5RatingsOldNotes = (
    ratingsWithNoteLabelInfo[
      (
        noteCreatedBeforeNoteStatusHistory
        & ratingsWithNoteLabelInfo[c.ratingCreatedBeforePublicTSVReleasedKey]
      )
    ][[c.raterParticipantIdKey, c.noteIdKey, c.createdAtMillisKey]]
    .sort_values(c.createdAtMillisKey)
    .groupby(c.noteIdKey)
    .head(_maxHistoricalValidRatings)
  )[[c.raterParticipantIdKey, c.noteIdKey]].merge(ratingsWithNoteLabelInfo)

  ratingsBeforeStatusNewNotes = ratingsWithNoteLabelInfo[
    (
      np.invert(noteCreatedBeforeNoteStatusHistory)
      & ratingsWithNoteLabelInfo[c.ratingCreatedBeforePublicTSVReleasedKey]
      & ratingsWithNoteLabelInfo[c.ratingCreatedBeforeMostRecentNMRLabelKey]
    )
  ]

  combinedRatingsBeforeStatus = pd.concat([ratingsBeforeStatusNewNotes, first5RatingsOldNotes])

  if logging:
    print(
      f"Total ratings: {np.invert(noteCreatedBeforeNoteStatusHistory).sum()} post-tombstones and {(noteCreatedBeforeNoteStatusHistory).sum()} pre-tombstones"
    )
    print(
      f"Total ratings created before statuses: {len(combinedRatingsBeforeStatus)}, including {len(ratingsBeforeStatusNewNotes)} post-tombstones and {len(first5RatingsOldNotes)} pre-tombstones."
    )

  assert len(combinedRatingsBeforeStatus) <= len(ratingsWithNoteLabelInfo)
  return combinedRatingsBeforeStatus


def get_ratings_with_scores(
  ratings: pd.DataFrame,
  noteStatusHistory: pd.DataFrame,
  scoredNotes: pd.DataFrame,
  logging: bool = True,
  doTypeCheck: bool = True,
) -> pd.DataFrame:
  """
  This funciton merges the note status history, ratings, and scores for later aggregation.

  Args:
      ratings (pd.DataFrame): all ratings
      noteStatusHistory (pd.DataFrame): history of note statuses
      scoredNotes (pd.DataFrame): Notes scored from MF + contributor stats
  Returns:
      pd.DataFrame: binaryRatingsOnNotesWithStatusLabels Binary ratings with status labels
  """
  ratingsBeforeNoteStatus = get_ratings_before_note_status_and_public_tsv(
    ratings, noteStatusHistory, logging, doTypeCheck
  )

  ratingsWithScores = ratingsBeforeNoteStatus[
    [c.raterParticipantIdKey, c.helpfulNumKey, c.noteIdKey]
  ].merge(
    scoredNotes[
      [
        c.noteIdKey,
        c.currentlyRatedHelpfulBoolKey,
        c.currentlyRatedNotHelpfulBoolKey,
        c.awaitingMoreRatingsBoolKey,
      ]
    ],
    on=c.noteIdKey,
  )
  return ratingsWithScores


def get_valid_ratings(
  ratings: pd.DataFrame,
  noteStatusHistory: pd.DataFrame,
  scoredNotes: pd.DataFrame,
  logging: bool = True,
  doTypeCheck: bool = True,
) -> pd.DataFrame:
  """Determine which ratings are "valid" (used to determine rater helpfulness score)

  See definition here: https://twitter.github.io/communitynotes/contributor-scores/#valid-ratings

  Args:
      ratings (pd.DataFrame)
      noteStatusHistory (pd.DataFrame)
      scoredNotes (pd.DataFrame)
      logging (bool, optional): Defaults to True.
      doTypeCheck (bool): do asserts to check types.
  Returns:
      pd.DataFrame: binaryRatingsOnNotesWithStatusLabels CRH/CRNH notes group by helpfulness
  """
  ratingsWithScores = get_ratings_with_scores(
    ratings, noteStatusHistory, scoredNotes, logging, doTypeCheck
  )
  ratingsWithScores[c.ratingCountKey] = 1

  binaryRatingsOnNotesWithStatusLabels = ratingsWithScores[
    (
      ratingsWithScores[c.currentlyRatedHelpfulBoolKey]
      | ratingsWithScores[c.currentlyRatedNotHelpfulBoolKey]
    )
    & ((ratingsWithScores[c.helpfulNumKey] == 1) | (ratingsWithScores[c.helpfulNumKey] == 0))
  ].copy()

  helpfulRatingOnCrhNote = (
    binaryRatingsOnNotesWithStatusLabels[c.currentlyRatedHelpfulBoolKey]
  ) & (binaryRatingsOnNotesWithStatusLabels[c.helpfulNumKey] == 1)
  notHelpfulRatingOnCrhNote = (
    binaryRatingsOnNotesWithStatusLabels[c.currentlyRatedHelpfulBoolKey]
  ) & (binaryRatingsOnNotesWithStatusLabels[c.helpfulNumKey] == 0)
  helpfulRatingOnCrnhNote = (
    binaryRatingsOnNotesWithStatusLabels[c.currentlyRatedNotHelpfulBoolKey]
  ) & (binaryRatingsOnNotesWithStatusLabels[c.helpfulNumKey] == 1)
  notHelpfulRatingOnCrnhNote = (
    binaryRatingsOnNotesWithStatusLabels[c.currentlyRatedNotHelpfulBoolKey]
  ) & (binaryRatingsOnNotesWithStatusLabels[c.helpfulNumKey] == 0)

  binaryRatingsOnNotesWithStatusLabels[c.successfulRatingHelpfulCount] = False
  binaryRatingsOnNotesWithStatusLabels[c.successfulRatingNotHelpfulCount] = False
  binaryRatingsOnNotesWithStatusLabels[c.successfulRatingTotal] = False
  binaryRatingsOnNotesWithStatusLabels[c.unsuccessfulRatingHelpfulCount] = False
  binaryRatingsOnNotesWithStatusLabels[c.unsuccessfulRatingNotHelpfulCount] = False
  binaryRatingsOnNotesWithStatusLabels[c.unsuccessfulRatingTotal] = False
  binaryRatingsOnNotesWithStatusLabels[c.ratingAgreesWithNoteStatusKey] = False

  binaryRatingsOnNotesWithStatusLabels.loc[
    helpfulRatingOnCrhNote,
    c.successfulRatingHelpfulCount,
  ] = True
  binaryRatingsOnNotesWithStatusLabels.loc[
    notHelpfulRatingOnCrnhNote,
    c.successfulRatingNotHelpfulCount,
  ] = True
  binaryRatingsOnNotesWithStatusLabels.loc[
    helpfulRatingOnCrhNote | notHelpfulRatingOnCrnhNote,
    c.successfulRatingTotal,
  ] = True
  binaryRatingsOnNotesWithStatusLabels.loc[
    notHelpfulRatingOnCrhNote,
    c.unsuccessfulRatingHelpfulCount,
  ] = True
  binaryRatingsOnNotesWithStatusLabels.loc[
    helpfulRatingOnCrnhNote,
    c.unsuccessfulRatingNotHelpfulCount,
  ] = True
  binaryRatingsOnNotesWithStatusLabels.loc[
    notHelpfulRatingOnCrhNote | helpfulRatingOnCrnhNote,
    c.unsuccessfulRatingTotal,
  ] = True
  binaryRatingsOnNotesWithStatusLabels.loc[
    helpfulRatingOnCrhNote | notHelpfulRatingOnCrnhNote, c.ratingAgreesWithNoteStatusKey
  ] = True

  if logging:
    print(f"Total valid ratings: {len(binaryRatingsOnNotesWithStatusLabels)}")

  return binaryRatingsOnNotesWithStatusLabels


def compute_note_stats(ratings: pd.DataFrame, noteStatusHistory: pd.DataFrame) -> pd.DataFrame:
  """Compute aggregate note statics over available ratings and merge in noteStatusHistory fields.

  This function computes note aggregates over ratings and then merges additional fields from
  noteStatusHistory.  In general, we do not expect that every note in noteStatusHistory will
  also appear in ratings (e.g. some notes have no ratings) so the aggregate values for some
  notes will be NaN.  We do expect that all notes observed in ratings will appear in
  noteStatusHistory, and verify that expectation with an assert.

  Note that the content of both ratings and noteStatusHistory may vary across callsites.  For
  example:
  * Scoring models operating on subsets of notes and ratings may pre-filter both
    ratings and noteStatusHistory to only include notes/ratings that are in-scope.
  * During meta scoring we may invoke compute_note_stats with the full set of ratings
    and notes to compute note stats supporting contributor helpfulness aggregates.

  Args:
    ratings (pd.DataFrame): all ratings
    noteStatusHistory (pd.DataFrame): history of note statuses
  Returns:
    pd.DataFrame containing stats about each note
  """
  last28Days = (
    1000
    * (
      datetime.fromtimestamp(c.epochMillis / 1000, tz=timezone.utc)
      - timedelta(days=c.emergingWriterDays)
    ).timestamp()
  )
  ratingsToUse = pd.DataFrame(
    ratings[[c.noteIdKey] + c.helpfulTagsTSVOrder + c.notHelpfulTagsTSVOrder]
  )
  ratingsToUse.loc[:, c.numRatingsKey] = 1
  ratingsToUse.loc[:, c.numRatingsLast28DaysKey] = False
  ratingsToUse.loc[ratings[c.createdAtMillisKey] > last28Days, c.numRatingsLast28DaysKey] = True
  noteStats = ratingsToUse.groupby(c.noteIdKey).sum()

  noteStats = noteStats.merge(
    noteStatusHistory[
      [
        c.noteIdKey,
        c.createdAtMillisKey,
        c.noteAuthorParticipantIdKey,
        c.classificationKey,
        c.currentLabelKey,
        c.lockedStatusKey,
      ]
    ],
    on=c.noteIdKey,
    how="outer",
  )

  columns = [
    c.numRatingsKey,
    c.numRatingsLast28DaysKey,
  ] + (c.helpfulTagsTSVOrder + c.notHelpfulTagsTSVOrder)
  noteStats = noteStats.fillna({col: 0 for col in columns})
  noteStats[columns] = noteStats[columns].astype(np.int64)

  assert len(noteStats) == len(noteStatusHistory), "noteStatusHistory should contain all notes"
  return noteStats


def compute_scored_notes(
  ratings: pd.DataFrame,
  noteParams: pd.DataFrame,
  raterParams: Optional[pd.DataFrame],
  noteStatusHistory: pd.DataFrame,
  minRatingsNeeded: int,
  crhThreshold: float,
  crnhThresholdIntercept: float,
  crnhThresholdNoteFactorMultiplier: float,
  crnhThresholdNMIntercept: float,
  crhSuperThreshold: float,
  inertiaDelta: float,
  finalRound: bool = False,
  is_crh_function: Callable[..., pd.Series] = is_crh,
  is_crnh_function: Callable[..., pd.Series] = is_crnh,
) -> pd.DataFrame:
  """
  Merges note status history, ratings, and model output. It annotes the data frame with
  different note statuses, and features needed to calculate contributor stats.

  Args:
      ratings: All ratings from Community Notes contributors.
      noteParams: Note intercepts and factors returned from matrix factorization.
      raterParams: Rater intercepts and factors returned from matrix factorization.
      noteStatusHistory: History of note statuses.
      minRatingsNeeded: Minimum number of ratings for a note to achieve status.
      crhThrehsold: Minimum intercept for most notes to achieve CRH status.
      crnhThresholdIntercept: Minimum intercept for most notes to achieve CRNH status.
      crnhThresholdNoteFactorMultiplier: Scaling factor making controlling the relationship between
        CRNH threshold and note intercept.  Note that this constant is set negative so that notes with
        larger (magnitude) factors must have proportionally lower intercepts to become CRNH.
      crnhThresholdNMIntercept: Minimum intercept for notes which do not claim a tweet is misleading
        to achieve CRNH status.
      crhSuperThreshold: Minimum intercept for notes which have consistent and common patterns of
        repeated reason tags in not-helpful ratings to achieve CRH status.
      inertiaDelta: Minimum amount which a note that has achieve CRH status must drop below the
        applicable threshold to lose CRH status.
      finalRound: If true, enable additional status assignment logic which is only applied when
        determining final status.  Given that these mechanisms add complexity we don't apply them
        in earlier rounds.
      is_crh_function: Function specifying default CRH critierai.
      is_crnh_function: Function specifying default CRNH critierai.
  Returns:
      pd.DataFrame: scoredNotes The scored notes
  """
  noteStats = compute_note_stats(ratings, noteStatusHistory)
  noteStats = noteStats.drop(
    columns=[
      c.numRatingsLast28DaysKey,
      c.createdAtMillisKey,
    ]
  )
  noteParamsColsToKeep = [c.noteIdKey, c.internalNoteInterceptKey, c.internalNoteFactor1Key]
  for col in c.noteParameterUncertaintyTSVColumns:
    if col in noteParams.columns:
      noteParamsColsToKeep.append(col)
  noteStats = noteStats.merge(noteParams[noteParamsColsToKeep], on=c.noteIdKey, how="left")

  rules = [
    scoring_rules.DefaultRule(RuleID.INITIAL_NMR, set(), c.needsMoreRatings),
    scoring_rules.RuleFromFunction(
      RuleID.GENERAL_CRH,
      {RuleID.INITIAL_NMR},
      c.currentlyRatedHelpful,
      lambda noteStats: is_crh_function(noteStats, minRatingsNeeded, crhThreshold),
    ),
    scoring_rules.RuleFromFunction(
      RuleID.GENERAL_CRNH,
      {RuleID.INITIAL_NMR},
      c.currentlyRatedNotHelpful,
      lambda noteStats: is_crnh_function(
        noteStats, minRatingsNeeded, crnhThresholdIntercept, crnhThresholdNoteFactorMultiplier
      ),
    ),
    scoring_rules.NMtoCRNH(
      RuleID.NM_CRNH, {RuleID.INITIAL_NMR}, c.currentlyRatedNotHelpful, crnhThresholdNMIntercept
    ),
  ]
  if finalRound:
    tagAggregates = tag_filter.get_note_tag_aggregates(ratings, noteParams, raterParams)
    assert len(tagAggregates) == len(noteParams), "there should be one aggregate per scored note"
    noteStats = tagAggregates.merge(noteStats, on=c.noteIdKey, how="outer")

    rules.extend(
      [
        scoring_rules.AddCRHInertia(
          RuleID.GENERAL_CRH_INERTIA,
          {RuleID.GENERAL_CRH},
          c.currentlyRatedHelpful,
          crhThreshold - inertiaDelta,
          crhThreshold,
          minRatingsNeeded,
        ),
        scoring_rules.FilterTagOutliers(
          RuleID.TAG_OUTLIER,
          {RuleID.GENERAL_CRH},
          c.needsMoreRatings,
          crhSuperThreshold,
        ),
        scoring_rules.AddCRHInertia(
          RuleID.ELEVATED_CRH_INERTIA,
          {RuleID.TAG_OUTLIER},
          c.currentlyRatedHelpful,
          crhSuperThreshold - inertiaDelta,
          crhSuperThreshold,
          minRatingsNeeded,
        ),
      ]
    )
  scoredNotes = scoring_rules.apply_scoring_rules(
    noteStats, rules, c.internalRatingStatusKey, c.internalActiveRulesKey
  )
  scoredNotes = scoredNotes.drop(columns=[c.lockedStatusKey])

  return scoredNotes