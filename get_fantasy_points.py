
# import ruleset
# import pandas as pd

# doesn't cover every rule, just the main ones.
# some stats are not as easily extracted
def getBasePoints( rs, plyr ):
    # missing bonus 40 and 50 yd tds here. other method getBonusPoints, which looks at weekly stats??
    # also missing extra points for longer field goals
    # note: for some reason a line like
    # + some_val
    # is valid python, but will not get added
    fpts = \
    + rs.ppPY * plyr.passing_yds \
    + rs.ppPY25 * (plyr.passing_yds / 25) \
    + rs.ppPC * plyr.passing_cmp \
    + rs.ppINC * (plyr.passing_att - plyr.passing_cmp) \
    + rs.ppPTD * plyr.passing_tds \
    + rs.ppINT * plyr.passing_int \
    + rs.pp2PC * plyr.passing_twoptm \
    + rs.ppRY * plyr.rushing_yds \
    + rs.ppRY10 * (plyr.rushing_yds / 10) \
    + rs.ppRTD * plyr.rushing_tds \
    + rs.pp2PR * plyr.rushing_twoptm \
    + rs.ppREY * plyr.receiving_yds \
    + rs.ppREY10 * (plyr.receiving_yds / 10) \
    + rs.ppREC * plyr.receiving_rec \
    + rs.ppRETD * plyr.receiving_tds \
    + rs.pp2PRE * plyr.receiving_twoptm \
    + rs.ppFUML * plyr.fumbles_lost \
    + rs.ppPAT * plyr.kicking_xpmade \
    + rs.ppFGM * (plyr.kicking_fga - plyr.kicking_fgm) \
    + rs.ppFG0 * plyr.kicking_fgm
    return fpts

# def decorateDataFrame( rs, df, name='fantasy_points' ):
#     """
#     repeating ourselves a lot here; it would be good to combine code w/ above (or only use this)
#     rs: rule set
#     df: dataframe containing player stats
#     name: name of decorated stat
#     """
#     df[name] = \
#     + rs.ppPY * df['passing_yds'] \
#     + rs.ppPY25 * (df['passing_yds'] / 25) \
#     + rs.ppPC * df['passing_cmp'] \
#     + rs.ppINC * (df['passing_att'] - df['passing_cmp']) \
#     + rs.ppPTD * df['passing_tds'] \
#     + rs.ppINT * df['passing_int'] \
#     + rs.pp2PC * df['passing_twoptm'] \
#     + rs.ppRY * df['rushing_yds'] \
#     + rs.ppRY10 * (df['rushing_yds'] / 10) \
#     + rs.ppRTD * df['rushing_tds'] \
#     + rs.pp2PR * df['rushing_twoptm'] \
#     + rs.ppREY * df['receiving_yds'] \
#     + rs.ppREY10 * (df['receiving_yds'] / 10) \
#     + rs.ppREC * df['receiving_rec'] \
#     + rs.ppRETD * df['receiving_tds'] \
#     + rs.pp2PRE * df['receiving_twoptm'] \
#     + rs.ppFUML * df['fumbles_lost'] \
#     + rs.ppPAT * df['kicking_xpmade'] \
#     + rs.ppFGM * (df['kicking_fga'] - df['kicking_fgm']) \
#     + rs.ppFG0 * df['kicking_fgm']
#     return df

# with get() this should work from a dictionary or a dataframe
def get_points( rs, df ):
    """
    repeating ourselves a lot here; it would be good to combine code w/ above (or only use this)
    rs: rule set
    df: dataframe containing player stats
    name: name of decorated stat
    """
    gf = lambda key: df.get(key, 0)
    return \
    rs.ppPY * gf('pass_yds') \
    + rs.ppPY25 * (gf('pass_yds') // 25) \
    + rs.ppPC * gf('pass_cmp') \
    + rs.ppINC * (gf('pass_att') - gf('pass_cmp')) \
    + rs.ppPTD * gf('pass_td') \
    + rs.ppINT * gf('pass_int') \
    + rs.pp2PC * gf('pass_twoptm') \
    + rs.ppRY * gf('rush_yds') \
    + rs.ppRY10 * (gf('rush_yds') // 10) \
    + rs.ppRTD * gf('rush_td') \
    + rs.pp2PR * gf('rush_twoptm') \
    + rs.ppREY * gf('rec_yds') \
    + rs.ppREY10 * (gf('rec_yds') // 10) \
    + rs.ppREC * gf('rec') \
    + rs.ppRETD * gf('rec_td') \
    + rs.pp2PRE * gf('rec_twoptm') \
    + rs.ppFUML * gf('fumbles_lost') \
    + rs.ppPAT * gf('xpm') \
    + rs.ppFGM * (gf('fga') - gf('fgm')) \
    + rs.ppFG0 * gf('fgm')
    # return df
# could manually suffix these w/ "kick_". note that we can probably use more stats now (with PFR).
# TODO: missing: missed PATs (ppPATM)
