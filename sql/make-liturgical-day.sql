
drop table if exists liturgical_day;

create table liturgical_day
(lit_day_id VARCHAR(64) primary key,
lit_day_order int,
slug    varchar(40),
title   varchar(200),
season  varchar(10),
subseason   varchar(10),
wknum   smallint,
seq smallint,
lit_rank varchar(40) NOT NULL DEFAULT 'UNDETERMINED',
CONSTRAINT fk_litday_rank FOREIGN KEY (lit_rank) REFERENCES p_lit_rank(rank_code) ON UPDATE CASCADE ON DELETE RESTRICT
);

insert into liturgical_day
(lit_day_order,
lit_day_id,
season,
subseason,
wknum,
seq)
with nums as (
    select help_topic_id as mynum
    from mysql.help_topic
    where help_topic_id < 300
)
, fulllist as (
    select 
        season
        , subseason 
        , wknums.mynum as wknum
        , seqs.mynum as seq
        , ls.season_rnk
        , ls.subseason_order
    from liturgical_subseasons ls 
    join nums wknums
    join nums seqs
    on wknums.mynum between ls.minwk and ls.maxwk 
    and seqs.mynum between ls.minseq and ls.maxseq 
)
, filtered as (
select *
from fulllist
where 
    not ( season='ADV' and subseason='I' and wknum=3 and seq=7) -- Third Saturday of Advent is always an O Antiphon
    and not (season='TQ' and subseason='LENT' and wknum=0 and seq<4) -- Partial week of Ash Wednesday
    and not (season='PASC' and subseason='AD_ASC' and wknum=2 and seq=1) -- Divine Mercy Sunday is part of the Octave
    and not (season='PASC' and subseason='OCT' and wknum=2 and seq>1) -- Only Divine Mercy Sunday is part of the Octave
    and not (season='PASC' and subseason='POST_ASC' and wknum=6 and seq<6) -- Sixth week of Easter before Ascension (in either calendar)
    )
select
rank() over (order by season_rnk, subseason_order, wknum, seq) as lit_day_order
, CONCAT(
            season,'-',
            subseason,
            '-', LPAD(wknum, 2, '0'),
            '-', LPAD(seq,   1, '0')
        ) as lit_day_id 
, season, subseason, wknum, seq
from filtered
order by 1;

update liturgical_day
join p_liturgical_day_slug_overrides 
using (lit_day_id)
set slug=slug_ovr
where slug_ovr is not null;


update liturgical_day
join p_liturgical_day_slug_overrides 
using (lit_day_id)
set title=title_ovr
where title_ovr is not null;

UPDATE liturgical_day
SET slug = 
    case 
        when season='ADV' and subseason='II'
        then case 
            when seq=1 
                then 'O-SAPIENTIA'
            when seq=2
                then 'O-ADONAI'
            when seq=3
                then 'O-RADIX-JESSE'
            when seq=4 
                then 'O-CLAVIS-DAVID'
            when seq=5 
                then 'O-ORIENS'
            when seq=6 
                then 'O-REX-GENTIUM'
            when seq=7 
                then 'O-EMMANUEL'
            when seq=8
                then 'DEC24'
            end
    when season='NAT' and subseason='IO' and seq=0
        then 'HOLYFAMILY'
    else
        CONCAT(
            case when season not in ('OT','TQ') then concat(season,'-') else '' end,
            subseason,
            case when wknum <> 0 then concat('-', LPAD(wknum, 2, '0')) else '' end,
            case when seq <> 0 then concat('-', LPAD(seq,   1, '0')) else '' end
        )
    end
WHERE slug IS NULL;

update liturgical_day 
set title=concat(
         ELT( seq, 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
    , ' within the octave of '
    , case season 
        when 'ADV' then 'Advent'
        when 'NAT' then 'the Nativity'
        when 'TQ' then 'Lent'
        when 'PASC' then 'Easter' 
        when 'OT' then 'Ordinary Time' 
        end
)
where title is null
and subseason in ('IO','OCT');

update liturgical_day 
set title=concat(
         ELT( seq, 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
    , ' of Holy Week'
)
where title is null
and subseason in ('HOLYWEEK');

update liturgical_day 
set title=concat(
    cast(seq as char)
    , case when seq=1 then 'st' when seq in (2,22,32) then 'nd' when seq in (3,23, 33) then 'rd' else 'th' end
    , ' day after the Epiphany'
)
where title is null
and subseason='EPI';

update liturgical_day 
set title=concat(
    case when seq <> 1 then concat(
             ELT( seq, 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
             , ' of the ')
        else '' end
    , cast(wknum as char)
    , case when wknum=1 then 'st' when wknum in (2,22,32) then 'nd' when wknum in (3,23, 33) then 'rd' else 'th' end
    , ' '
    ,  case when seq = 1 then 'Sunday' else 'week' end
    , ' of '
    , case season 
        when 'ADV' then 'Advent'
        when 'NAT' then 'the Nativity'
        when 'TQ' then 'Lent'
        when 'PASC' then 'Easter' 
        when 'OT' then 'Ordinary Time' 
        end
)
where title is null;

