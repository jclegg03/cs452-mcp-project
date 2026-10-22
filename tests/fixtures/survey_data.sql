-- Synthetic, portable subset of the real schema used by the five examples.
-- Run only in a disposable test database, never in syp_dev.
CREATE TABLE Athlete (
  AthleteID varchar(36) PRIMARY KEY,
  Deactivated datetime(3) DEFAULT NULL
);
CREATE TABLE Team (TeamID varchar(6) PRIMARY KEY);
CREATE TABLE Compliance (
  AthleteID varchar(36) NOT NULL,
  TeamID varchar(6) NOT NULL,
  NumDailySubmitted int NOT NULL,
  NumWeeklySubmitted int NOT NULL,
  NumMonthlySubmitted int NOT NULL,
  BackgroundSubmitted int NOT NULL,
  FinalSubmitted int NOT NULL,
  IsActive int NOT NULL,
  PRIMARY KEY (TeamID, AthleteID),
  FOREIGN KEY (AthleteID) REFERENCES Athlete(AthleteID),
  FOREIGN KEY (TeamID) REFERENCES Team(TeamID)
);
CREATE TABLE Survey (
  SurveyID bigint PRIMARY KEY,
  AthleteID varchar(36) NOT NULL,
  SubmissionDate datetime(3) NOT NULL,
  FOREIGN KEY (AthleteID) REFERENCES Athlete(AthleteID)
);

INSERT INTO Team VALUES ('TEAM-X'), ('TEAM-Y'), ('EMPTY0');
INSERT INTO Athlete VALUES
  ('a-one', NULL), ('a-two', NULL), ('a-zero', NULL), ('a-off', NULL),
  ('a-dead', '2026-09-01 00:00:00.000000'), ('a-orphan', NULL), ('a-other', NULL);
INSERT INTO Compliance VALUES
  ('a-one', 'TEAM-X', 2, 1, 0, 1, 0, 1),
  ('a-one', 'TEAM-Y', 1, 0, 0, 0, 0, 1),
  ('a-two', 'TEAM-X', 2, 0, 0, 0, 1, 1),
  ('a-zero', 'TEAM-X', 0, 0, 0, 0, 0, 1),
  ('a-off', 'TEAM-X', 50, 0, 0, 0, 0, 0),
  ('a-dead', 'TEAM-X', 50, 0, 0, 0, 0, 1),
  ('a-other', 'TEAM-Y', 3, 0, 0, 0, 0, 1);
INSERT INTO Survey VALUES
  (1, 'a-one', '2026-10-15 00:00:00.000000'),
  (2, 'a-one', '2026-10-14 23:59:59.999999'),
  (3, 'a-two', '2026-10-21 12:00:00.000000'),
  (4, 'a-two', '2026-10-22 00:00:00.000000'),
  (5, 'a-off', '2026-10-18 12:00:00.000000'),
  (6, 'a-off', '2026-10-18 13:00:00.000000'),
  (7, 'a-off', '2026-10-18 14:00:00.000000'),
  (8, 'a-dead', '2026-10-20 12:00:00.000000'),
  (9, 'a-other', '2026-10-23 12:00:00.000000');
