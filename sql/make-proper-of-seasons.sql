drop table if exists proper_of_seasons; 


CREATE TABLE proper_of_seasons (
    -- Civil date (repeats across jurisdictions/calendars)
    dt DATE NOT NULL,

    -- Which calendar / usage this row belongs to (e.g., 'roman', 'us', 'diocese_xyz')
    jurisdiction VARCHAR(64) NOT NULL,

    -- Liturgical year key you use throughout your system
    liturgical_year SMALLINT UNSIGNED NOT NULL,

    -- Liturgical day identifier
    lit_day_id VARCHAR(64) NULL,

    -- Computed day-of-week with Sunday=1 ... Saturday=7
    wkday TINYINT UNSIGNED
        GENERATED ALWAYS AS (DAYOFWEEK(dt)) STORED,

    -- Computed lectionary cycles
    cycle_wk  TINYINT UNSIGNED
        GENERATED ALWAYS AS (MOD(liturgical_year, 2)) STORED,

    cycle_sun TINYINT UNSIGNED
        GENERATED ALWAYS AS (MOD(liturgical_year, 3)) STORED,

    PRIMARY KEY (jurisdiction, dt),

    INDEX idx_pos_jur_lityear_dt (jurisdiction, liturgical_year, dt),
    INDEX idx_pos_lit_day (lit_day_id),

    CONSTRAINT fk_pos_lit_day
        FOREIGN KEY (lit_day_id)
        REFERENCES liturgical_day(lit_day_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);



INSERT INTO proper_of_seasons (dt, jurisdiction, liturgical_year)
WITH RECURSIVE p1 AS (
  SELECT
    5 AS maxyears,
    DATE('2025-11-30') AS firstdate
),
years AS (
  -- y = 0..maxyears-1
  SELECT
    0 AS y,
    (SELECT firstdate FROM p1) AS y_start
  UNION ALL
  SELECT
    y + 1,
    DATE_ADD(y_start, INTERVAL 1 YEAR)
  FROM years, p1
  WHERE y + 1 < p1.maxyears
),
days AS (
  SELECT
    y.y,
    DATE_ADD(y.y_start, INTERVAL h.help_topic_id DAY) AS dt
  FROM years y
  JOIN mysql.help_topic h
    ON h.help_topic_id < DATEDIFF(DATE_ADD(y.y_start, INTERVAL 1 YEAR), y.y_start)
)
SELECT
  d.dt,
  'UNIVERSAL' AS jurisdiction,
  YEAR(d.dt) AS liturgical_year   -- placeholder
FROM days d;

-- --- ANCHOR DATES
-- Find the beginning of the Liturgical year
update proper_of_seasons ps
join liturgical_day ld
on season = 'ADV'
and subseason ='I'
and seq = 1
and wknum = 1
set ps.lit_day_id = ld.lit_day_id
where wkday = 1
AND dt BETWEEN
      DATE(CONCAT(YEAR(dt), '-11-27'))
      AND
      DATE(CONCAT(YEAR(dt), '-12-03'));

DELETE FROM proper_of_seasons
WHERE dt > (
  SELECT max_dt FROM (
    SELECT MAX(dt) AS max_dt
    FROM proper_of_seasons
    join liturgical_day ld using (lit_day_id)
    WHERE season='ADV' AND subseason='I' AND seq='1'
  ) x
);
  
-- Fix liturgical year  
UPDATE proper_of_seasons tgt
JOIN proper_of_seasons adv_start
  ON YEAR(tgt.dt) = YEAR(adv_start.dt)
 and tgt.jurisdiction = adv_start.jurisdiction
join liturgical_day ld
  on ld.lit_day_id =adv_start.lit_day_id 
 AND ld.season = 'ADV'
 AND ld.subseason = 'I'
 AND ld.seq = 1
 set tgt.liturgical_year = year(tgt.dt) + 1
 where tgt.dt >= adv_start.dt;
   
  
-- Epiphany
update proper_of_seasons ps
join liturgical_day ld
on season ='NAT' and subseason='EPI' and seq=0 and wknum=0
set ps.lit_day_id = ld.lit_day_id 
where month(dt)=1 and day(dt)=6 and jurisdiction = 'UNIVERSAL';


-- Baptism of the Lord
update proper_of_seasons ps
join liturgical_day ld
on season ='NAT' and subseason='BAPT'
set ps.lit_day_id = ld.lit_day_id 
where wkday = 1
    and jurisdiction = 'UNIVERSAL'
    and month(dt)=1 and day(dt) between 7 and 13;

    
-- Easter Sunday
update proper_of_seasons ps
join liturgical_day ld
on season='PASC' and subseason='OCT' and wknum=1 and seq=1
set ps.lit_day_id = ld.lit_day_id 
where ps.dt in (select dt from dates_for_easter);


 
-- Advent before Dec 17
UPDATE proper_of_seasons tgt
JOIN proper_of_seasons adv_start
  ON YEAR(tgt.dt) = YEAR(adv_start.dt)
 and tgt.jurisdiction = adv_start.jurisdiction
-- Identify the "start of Advent I" row via its liturgical_day attributes
JOIN liturgical_day ld_start
  ON ld_start.lit_day_id = adv_start.lit_day_id
 AND ld_start.season     = 'ADV'
 AND ld_start.subseason  = 'I'
 and ld_start.wknum      = 1
 AND ld_start.seq        = 1
-- Resolve the lit_day_id for each target date using computed wknum + seq
JOIN liturgical_day ld_tgt
  ON ld_tgt.season     = 'ADV'
 AND ld_tgt.subseason  = 'I'
 AND ld_tgt.wknum      = 1 + FLOOR(DATEDIFF(tgt.dt, adv_start.dt) / 7)
 AND ld_tgt.seq        = tgt.wkday
SET tgt.lit_day_id = ld_tgt.lit_day_id
WHERE tgt.dt BETWEEN adv_start.dt
                AND STR_TO_DATE(CONCAT(YEAR(adv_start.dt), '-12-16'), '%Y-%m-%d');

-- Dec 17–24 “O Antiphons” stretch
UPDATE proper_of_seasons tgt
JOIN liturgical_day ld_tgt
  ON ld_tgt.season     = 'ADV'
 AND ld_tgt.subseason  = 'II'
 AND ld_tgt.wknum      = 0
 AND ld_tgt.seq        = datediff( tgt.dt, date(concat(year(dt),'-12-16') ) )
 SET tgt.lit_day_id = ld_tgt.lit_day_id
WHERE tgt.dt BETWEEN STR_TO_DATE(CONCAT(YEAR(tgt.dt), '-12-17'), '%Y-%m-%d')
                AND STR_TO_DATE(CONCAT(YEAR(tgt.dt), '-12-24'), '%Y-%m-%d');



-- Nativity before Epiphany
update proper_of_seasons ps
JOIN liturgical_day ld
  on ld.season = 'NAT'
 and ld.subseason = case when ps.dt=date(concat( liturgical_year-1 ,'-12-25'))
                    then 'DAY'
                     when ps.dt < date(concat(liturgical_year,'-01-01') )
                    then 'IO'
                     when ps.dt = date(concat(liturgical_year,'-01-01') )
                    then 'OCT'
                    else 'PO' end
 and ld.seq = case when ps.dt=date(concat( liturgical_year-1 ,'-12-25'))       -- Christmas Day
                    then 0
                when ps.dt < date(concat(liturgical_year,'-01-01') )       -- Infra Octavam
                    then case 
                        when ps.wkday=1                    -- Holy Family on Sunday
                            then 0
                        when day(ps.dt)=30 and ps.wkday=6     -- Holy Family on Dec 30
                            then 0
                        else
                           datediff( ps.dt, concat( liturgical_year-1 ,'-12-25') ) 
                    end
                when ps.dt = date(concat(liturgical_year,'-01-01') )       -- Octave
                    then 0
                else                                                    -- Post Octavam
                    datediff( ps.dt, concat( liturgical_year ,'-01-01') )
                end
 SET ps.lit_day_id = ld.lit_day_id
where ps.dt BETWEEN
      DATE(CONCAT(liturgical_year-1, '-12-25'))
      AND
      DATE(CONCAT(liturgical_year, '-1-05'));
     




-- Nativity between Epiphany and Baptism
update
    proper_of_seasons ps
join proper_of_seasons epi
on
    ps.liturgical_year = epi.liturgical_year
    and epi.jurisdiction = ps.jurisdiction
join liturgical_day ld_epi
on
    epi.lit_day_id = ld_epi.lit_day_id
    and ld_epi.season = 'NAT'
    and ld_epi.subseason = 'EPI'
    and ld_epi.seq = 0
join proper_of_seasons bapt
on
    ps.liturgical_year = bapt.liturgical_year
    and bapt.jurisdiction = ps.jurisdiction
join liturgical_day ld_bapt
on
    ld_bapt.lit_day_id = bapt.lit_day_id
    and ld_bapt.season = 'NAT'
    and ld_bapt.subseason = 'BAPT'
    and ld_bapt.seq = 0
join liturgical_day ld
on
    ld.season = 'NAT'
    and ld.subseason = 'EPI'
    and ld.seq = datediff(ps.dt, epi.dt)
set
    ps.lit_day_id = ld.lit_day_id
where
    ps.dt between (
        epi.dt + interval 1 day
    ) and (
        bapt.dt - interval 1 day
    );


-- Easter season
update
    proper_of_seasons ps
join proper_of_seasons de
on
    de.liturgical_year = ps.liturgical_year
    and de.jurisdiction = ps.jurisdiction
join liturgical_day ld_de
on
    de.lit_day_id = ld_de.lit_day_id
    and ld_de.season = 'PASC'
    and ld_de.subseason = 'OCT'
    and ld_de.wknum = 1
    and ld_de.seq = 1
join liturgical_day ld
on
    ld.season = 'PASC'
    and ld.wknum = week(ps.dt) - week(de.dt) + 1
    and ld.seq = ps.wkday
    and ld.subseason = case 
        when ld.wknum=1 or (ld.wknum=2 and ld.seq=1)
            then 'OCT'
        when ld.wknum=8
            then 'PENT'
        when ld.wknum =6 and ld.seq = 5
            then 'ASC'
        when (ld.wknum = 6 and ld.seq > 5) or (ld.wknum=7)
            then 'POST_ASC'
        else 'AD_ASC'
    end
set
    ps.lit_day_id = ld.lit_day_id
where
    ps.dt between de.dt 
    and
    de.dt + interval 49 day;


-- Lent
update
    proper_of_seasons ps
join proper_of_seasons de
on
    de.liturgical_year = ps.liturgical_year
    and de.jurisdiction = ps.jurisdiction
join liturgical_day ld_de
on
    de.lit_day_id = ld_de.lit_day_id
    and ld_de.season = 'PASC'
    and ld_de.subseason = 'OCT'
    and ld_de.wknum = 1
    and ld_de.seq = 1
join liturgical_day ld
on ld.season='TQ'
and ( ( ld.subseason='LENT' and ld.wknum = week(ps.dt) - week(de.dt) + 7 )
    or ( ld.subseason='HOLYWEEK' and ld.wknum=0 and week(ps.dt) - week(de.dt) + 7 = 6))
and ld.seq = ps.wkday
set ps.lit_day_id=ld.lit_day_id
where ps.dt between de.dt - interval 46 day 
    and de.dt - interval 1 day ;



update proper_of_seasons ps
join proper_of_seasons bapt
on ps.liturgical_year = bapt.liturgical_year
join liturgical_day ld_bapt
on bapt.lit_day_id=ld_bapt.lit_day_id 
and ld_bapt.subseason='BAPT'
join proper_of_seasons tq
on ps.liturgical_year = tq.liturgical_year
join liturgical_day ld_tq
on tq.lit_day_id = ld_tq.lit_day_id 
and ld_tq.season ='TQ' and ld_tq.subseason='LENT' and ld_tq.wknum=0 and ld_tq.seq=4
join liturgical_day ld
on
    ld.season='OT'
    and ld.subseason='OT'
    and ld.wknum=week(ps.dt)-week(bapt.dt)+1
    and ld.seq=ps.wkday
set ps.lit_day_id = ld.lit_day_id 
where ps.dt between bapt.dt + interval 1 day and tq.dt - interval 1 day;


update proper_of_seasons ps
join proper_of_seasons pent
on ps.liturgical_year=pent.liturgical_year 
and ps.jurisdiction=pent.jurisdiction 
join liturgical_day ld_pent
on pent.lit_day_id=ld_pent.lit_day_id 
and ld_pent.subseason='PENT'
join proper_of_seasons adv
on (ps.liturgical_year+1)=adv.liturgical_year 
and ps.jurisdiction=adv.jurisdiction 
join liturgical_day ld_adv
on adv.lit_day_id =ld_adv.lit_day_id 
and ld_adv.season='ADV' and ld_adv.subseason='I' and ld_adv.wknum=1 and ld_adv.seq=1
join liturgical_day ld
on
    ld.season='OT'
    and ld.subseason='OT'
    and ld.wknum=week(ps.dt)-week(adv.dt)+35
    and ld.seq=ps.wkday 
set ps.lit_day_id=ld.lit_day_id 
where ps.dt between pent.dt + interval 1 day and adv.dt - interval 1 day;


-- Sunday of the Most Holy Trinity
update proper_of_seasons ps
join proper_of_seasons pent
on ps.liturgical_year=pent.liturgical_year 
and ps.jurisdiction=pent.jurisdiction 
join liturgical_day ld_pent
on pent.lit_day_id=ld_pent.lit_day_id 
and ld_pent.subseason='PENT'
join liturgical_day ld
on
    ld.season='OT'
    and ld.subseason='FOL'
    and ld.wknum=0
    and ld.seq=0
set ps.lit_day_id = ld.lit_day_id 
where ps.dt = pent.dt + interval 7 day;

-- - US OVERRIDES
-- Epiphany - US
insert into proper_of_seasons 
(dt, lit_day_id , jurisdiction, liturgical_year)
(
select dt,
    (select lit_day_id from liturgical_day
        where season='NAT' and subseason='EPI' and wknum=0 and seq=0)
    , 'US', liturgical_year 
from proper_of_seasons 
where wkday = 1 and month(dt) =1 and dayofmonth(dt) between 2 and 8 );


-- Baptism of the Lord - US
INSERT INTO proper_of_seasons (dt, jurisdiction, liturgical_year, lit_day_id)
SELECT
    x.us_dt,
    'US' AS jurisdiction,
    u2.liturgical_year,
    u2.lit_day_id
FROM (
    SELECT
        CASE
            WHEN DAYOFMONTH(u.dt) BETWEEN 7 AND 8 THEN u.dt + INTERVAL 1 DAY
            ELSE u.dt
        END AS us_dt
    FROM proper_of_seasons u
    JOIN liturgical_day ld
      ON ld.lit_day_id = u.lit_day_id
    WHERE u.jurisdiction = 'UNIVERSAL'
      AND ld.season = 'NAT'
      AND ld.subseason = 'BAPT'
) x
JOIN proper_of_seasons u2
  ON u2.jurisdiction = 'UNIVERSAL'
 AND u2.dt = x.us_dt;


-- Epiphany season - US
-- Epiphany season - US (new schema)
INSERT INTO proper_of_seasons (dt, jurisdiction, liturgical_year, lit_day_id)
WITH
bapt AS (
    SELECT dt, liturgical_year
    FROM proper_of_seasons
    WHERE jurisdiction = 'US'
      AND lit_day_id IS NOT NULL
      AND lit_day_id IN (
          SELECT lit_day_id FROM liturgical_day
          WHERE season = 'NAT' AND subseason = 'BAPT'
      )
),
epi AS (
    SELECT dt, liturgical_year
    FROM proper_of_seasons
    WHERE jurisdiction = 'US'
      AND lit_day_id IS NOT NULL
      AND lit_day_id IN (
          SELECT lit_day_id FROM liturgical_day
          WHERE season = 'NAT' AND subseason = 'EPI'
      )
),
dates AS (
    SELECT dt, liturgical_year
    FROM proper_of_seasons
    WHERE jurisdiction = 'UNIVERSAL'
)
SELECT
    dts.dt,
    'US' AS jurisdiction,
    dts.liturgical_year,
    ld.lit_day_id
FROM dates dts
JOIN bapt bp
  ON dts.liturgical_year = bp.liturgical_year
 AND dts.dt < bp.dt
JOIN epi ep
  ON dts.liturgical_year = ep.liturgical_year
 AND dts.dt > ep.dt
JOIN liturgical_day ld
  ON ld.season    = 'NAT'
 AND ld.subseason = 'EPI'
 AND ld.wknum     = 0
 AND ld.seq       = DATEDIFF(dts.dt, ep.dt);

-- US overrides for Jan 6/Jan 7 when Epiphany is transferred to the following Sunday (Jan 7 or Jan 8)
INSERT IGNORE INTO proper_of_seasons (dt, jurisdiction, liturgical_year, lit_day_id)
WITH us_epi AS (
    -- US Epiphany day (seq=0) for each liturgical year
    SELECT ps.dt, ps.liturgical_year
    FROM proper_of_seasons ps
    JOIN liturgical_day ld
      ON ld.lit_day_id = ps.lit_day_id
    WHERE ps.jurisdiction = 'US'
      AND ld.season = 'NAT'
      AND ld.subseason = 'EPI'
      AND ld.seq = 0
),
candidates AS (
    -- Pull Jan 6 and Jan 7 from UNIVERSAL only for years where US Epiphany falls on Jan 7 or Jan 8
    SELECT u.dt, u.liturgical_year
    FROM proper_of_seasons u
    JOIN us_epi e
      ON e.liturgical_year = u.liturgical_year
    WHERE u.jurisdiction = 'UNIVERSAL'
      AND MONTH(u.dt) = 1
      AND DAYOFMONTH(u.dt) IN (6, 7)
      AND e.dt IN (
          DATE(CONCAT(YEAR(e.dt), '-01-07')),
          DATE(CONCAT(YEAR(e.dt), '-01-08'))
      )
      AND u.dt < e.dt
)
SELECT
    c.dt,
    'US' AS jurisdiction,
    c.liturgical_year,
    ld.lit_day_id
FROM candidates c
JOIN liturgical_day ld
  ON ld.season    = 'NAT'
 AND ld.subseason = 'PO'
 AND ld.seq       = CASE DAYOFMONTH(c.dt)
                        WHEN 6 THEN 5
                        WHEN 7 THEN 6
                    END;


-- Pre-Ascension - US
INSERT IGNORE INTO proper_of_seasons (dt, jurisdiction, liturgical_year, lit_day_id)
SELECT
    u.dt,
    'US' AS jurisdiction,
    u.liturgical_year,
    ld_us.lit_day_id
FROM proper_of_seasons u
JOIN liturgical_day ld_u
  ON ld_u.lit_day_id = u.lit_day_id
JOIN liturgical_day ld_us
  ON ld_us.season    = ld_u.season
 AND ld_us.wknum      = ld_u.wknum
 AND ld_us.seq        = ld_u.seq
 AND ld_us.subseason  = 'AD_ASC'
WHERE u.jurisdiction = 'UNIVERSAL'
  AND ld_u.season = 'PASC'
  AND ld_u.wknum = 6
  AND (ld_u.subseason IS NULL OR ld_u.subseason <> 'AD_ASC');


-- Ascension - US
INSERT IGNORE INTO proper_of_seasons (dt, jurisdiction, liturgical_year, lit_day_id)
SELECT
    u.dt,
    'US' AS jurisdiction,
    u.liturgical_year,
    ld_us.lit_day_id
FROM proper_of_seasons u
JOIN liturgical_day ld_u
  ON ld_u.lit_day_id = u.lit_day_id
JOIN liturgical_day ld_us
  ON ld_us.subseason  = 'ASC'
WHERE u.jurisdiction = 'UNIVERSAL'
  AND u.wkday = 1
  AND ld_u.season = 'PASC'
  AND ld_u.wknum = 7
  AND ld_u.seq = 1;



-- 
select pos.*, ld.title from proper_of_seasons pos
left join liturgical_day ld on pos.lit_day_id = ld.lit_day_id 
order by dt, jurisdiction;

select pos.*, ld.title from proper_of_seasons pos
left join liturgical_day ld on pos.lit_day_id = ld.lit_day_id 
where season is null or subseason is null or wknum is null or seq is null
order by dt, jurisdiction;

SELECT MAX(diff) AS max_gap
FROM (
  select
  datediff( ps.dt, LAG(ps.dt) OVER (ORDER BY ps.dt)) AS diff
  FROM proper_of_seasons ps
) t;




