#!/usr/bin/env python3
from __future__ import division
from __future__ import print_function
from builtins import input
from builtins import range
import numpy as np
import scipy.stats
import sys
import os.path
import argparse
import random
import logging
from itertools import takewhile
from difflib import get_close_matches
from cmd import Cmd
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from tools import *
from get_fantasy_points import get_points
from ruleset import bro_league, phys_league, dude_league, nycfc_league, ram_league


## slap these bye/injury factors up here for now
# 13 games in regular FF season, but we're going to playoffs. we'll pretend they're independent.
# 17 weeks in season, 16 games played by each team, any reasonable league plays the first 16 so 15/16
bye_factor = (16-1)/16
# this is the approximate fraction of the time that a player in
#  each position spends on the field uninjured.
# from sportinjurypredictor.net, based on average games missed assuming a 17 game season
# obviously rough, but captures trend and follows intuition
pos_injury_factor = {'QB':0.94, 'RB':0.85, 'WR':0.89, 'TE':0.89, 'DST':1.0, 'K':1.0}


def evaluate_roster(rosdf, n_roster_per_team, flex_pos, outfile=None):
    """
    applies projection for season points, with an approximation for bench value
    returns tuple of starter, bench value
    """
    numplayers = len(rosdf)
    numroster = sum([n_roster_per_team[pos] for pos in n_roster_per_team])
    if numplayers < numroster:
        print('This roster is not full.', file=outfile)
    if numplayers > numroster:
        print('This roster has too many players.', file=outfile)
    
    starterval, benchval = 0, 0
    i_st = [] # the indices of the players we have counted so far
    for pos in ['QB', 'RB', 'WR', 'TE', 'K', 'DST']:
        n_starters = n_roster_per_team[pos]
        rospos = rosdf[rosdf.pos == pos].sort_values('exp_proj', ascending=False)
        i_stpos = rospos.index[:n_starters]
        val = rospos[rospos.index.isin(i_stpos)]['exp_proj'].sum()
        starterval = starterval + val
        i_st.extend(i_stpos)

    n_flex = n_roster_per_team['FLEX']
    rosflex = rosdf[(~rosdf.index.isin(i_st)) & (rosdf.pos.isin(flex_pos))].sort_values('exp_proj', ascending=False)
    i_flex = rosflex.index[:n_flex]
    starterval = starterval + rosflex[rosflex.index.isin(i_flex)]['exp_proj'].sum()
    i_st.extend(i_flex)
    
    print('  starting lineup:', file=outfile)
    startdf = rosdf[rosdf.index.isin(i_st)].drop(['vols', 'volb', 'vbsd', 'adp', 'ecp', 'tier'], axis=1)
    print(startdf, file=outfile)

    benchdf = rosdf[~rosdf.index.isin(i_st)].drop(['vols', 'volb', 'vbsd', 'adp', 'ecp', 'tier'], axis=1)
    if len(benchdf) > 0:
        print('  bench:', file=outfile)
        print(benchdf, file=outfile)

    ## we're gonna do a really dumb estimation for bench value
    # and pretend that the chance of a bench player being used
    # is the same as that of a starter being out.
    # we'll multiply by the number of starters at that position divided by
    # the number of bench players to make this a little better.
    # ignoring all complicated combinatorics and pretending the probabilities are small.
    # we'll call this an "index" to avoid commiting to a meaning right now :)

    for _,row in benchdf.iterrows():
        pos = row.pos
        start_to_bench_ratio = len(startdf[startdf.pos == pos]) * 1.0 / len(benchdf[benchdf.pos == pos])
        # TODO: evaluate bench players (and other players) with the same method used for VBSD / auction price.
        benchval = benchval + (1 - bye_factor*pos_injury_factor[pos])*row.exp_proj
        
    auctionval = rosdf['auction'].sum()

    # round values to whole numbers for josh, who doesn't like fractions :)
    print('\nprojected starter points:\t{}'.format(int(round(starterval))), file=outfile)
    print('estimated bench value:\t\t{}'.format(int(round(benchval))), file=outfile)
    print('total points:\t\t\t{}'.format(int(round(benchval + starterval))), file=outfile)
    print('approximate auction value:\t${:.2f}\n'.format(auctionval), file=outfile)
    return starterval, benchval

def find_by_team(team, ap, pp):
    """
    prints players on the given team
    """
    available = ap[ap.team == team.upper()]
    if len(available) > 0:
        print('Available players:')
        print(available)
    else:
        print('No available players found on team {}'.format(team))
    picked = pp[pp.team == team.upper()]
    if len(picked) > 0:
        print('Picked players:')
        print(picked)
    
def find_handcuff(index, ap, pp):
    """
    prints a list of players with the same team and position as the indexed player.
    ap: dataframe of available players
    pp: dataframe of picked players
    """
    player = pd.concat([ap, pp], sort=False).loc[index]
    ## the "name" attribute is the index, so need dictionary syntax to grab actual name
    name, pos, team = player['player'], player.pos, player.team
    print('Looking for handcuffs for {} ({}) - {}...\n'.format(name, team, pos))
    ah = ap[(ap.pos == pos) & (ap.team == team) & (ap.player != name)]
    if len(ah) > 0:
        print('The following potential handcuffs are available:')
        print(ah.drop(['volb'], axis=1))
    ph = pp[(pp.pos == pos) & (pp.team == team) & (pp.player != name)]
    if len(ph) > 0:
        print('The following handcuffs have already been picked:')
        print(ph.drop(['volb'], axis=1))
    print() # end on a newline

# adding features to search by team name/city/abbreviation might be nice,
#   but probably not worth the time for the additional usefulness.
#   It could also complicate the logic and create edge cases.
def find_player(search_str, ap, pp):
    """
    prints the players with one of the words in search_words in their name.
    useful for finding which index certain players are if they are not in the top when drafted.
    search_words: list of words to look for
    ap: dataframe of available players
    pp: dataframe of picked players
    """
    # clean periods, since they aren't consistent between sources
    search_str = search_str.replace('.', '')
    # check if any of the search words are in the full name
    checkfunc = lambda name: all([sw in name.lower().replace('.', '') for sw in search_str.lower().split(' ')])
    filt_mask = pp.player.map(checkfunc) | pp.player.str.lower().isin( get_close_matches(search_str.lower(), pp.player.str.lower(), cutoff=0.8) )
    filtered_pp = pp[filt_mask]
    if filtered_pp.shape[0] > 0:
        print('\n  Picked players:')
        print(filtered_pp)
    filt_mask = ap.player.map(checkfunc) | ap.player.str.lower().isin( get_close_matches(search_str.lower(), ap.player.str.lower(), cutoff=0.8) )
    filtered_ap = ap[filt_mask]
    if filtered_ap.shape[0] == 0:
        print('\n  Could not find any available players.')
    else:
        print('\n  Available players:')
        print(filtered_ap)

def load_player_list(outname):
    """loads the available and picked player data from the label \"outname\""""
    print('Loading with label {}.'.format(outname))
    if os.path.isfile(outname+'.csv'):
        ap = pd.read_csv(outname+'.csv')
    else:
        logging.error('Could not find file {}.csv!'.format(outname))
    if os.path.isfile(outname+'_picked.csv'):
        pp = pd.read_csv(outname+'_picked.csv')
    else:
        logging.error('Could not find file {}_picked.csv!'.format(outname))
    return ap, pp
  
def pop_from_player_list(index, ap, pp=None, manager=None, pickno=None):
    """
    index: index of player to be removed from available
    """
    if index not in ap.index:
        raise IndexError('The index ({}) does not indicate an available player!'.format(index))
    player = ap.loc[index] # a dictionary of the entry
    # were using iloc, but the data may get re-organized so this should be safer
    if pp is not None:
        if len(pp[pp.index == index]) > 0:
            logging.error('It seems like the index of the player is already in the picked player list.')
            logging.error('Someone needs to clean up the logic...')
            logging.debug('picked players w/index:', pp.loc[index])
            logging.debug('available players w/index:', ap.loc[index])
        pp.loc[index] = player
        if manager is not None:
            pp.loc[index, 'manager'] = manager
             # this method of making the variable an integer is ugly and over time redundant.
            pp.manager = pp.manager.astype(int)
        if pickno is not None:
            pp.loc[index, 'pick'] = pickno
            pp.pick = pp.pick.astype(int)
        # player = df.pop(index) # DataFrame.pop pops a column, not a row
    name = player['player']
    pos = player['pos']
    team = player['team']
    print('selecting {} ({}) - {}'.format(name, team, pos))
    ap.drop(index, inplace=True)

def print_picked_players(pp, ap=None):
    """prints the players in dataframe df as if they have been selected"""
    npicked = pp.shape[0]
    if npicked == 0:
        print('No players have been picked yet.')
    else:
        with pd.option_context('display.max_rows', None):
            ## TODO: we can probably still stand to improve this output:'
            print(pp.drop([col for col in ['manager', 'pick', 'volb', 'tier'] if col in pp], axis=1))
        if ap is not None:
            print('\nTypically picked at this point (by ADP):')
            adpsort = pd.concat([pp, ap], sort=False).sort_values('adp', ascending=True).head(npicked)
            for pos in ['QB', 'RB', 'WR', 'TE', 'K', 'DST']:
                print ('{}:\t{}'.format(pos, len(adpsort[adpsort.pos == pos])))
        print('\nPlayers picked by position:')
        # to_string() suppresses the last line w/ "name" and "dtype" output
        print (pp.pos.value_counts().to_string())

def print_teams(ap, pp):
    """
    prints a list of teams in both the available and picked player lists
    """
    teams = pd.concat([ap, pp], sort=False)['team']
    print(teams.unique())

# this method will be our main output
def print_top_choices(df, ntop=10, npos=3, sort_key='vols', sort_asc=False, drop_stats=None, hide_pos=None):
    if sort_key is None:
        print('sorting by index')
        df.sort_index(ascending=sort_asc, inplace=True)
    else:
        df.sort_values(sort_key, ascending=sort_asc, inplace=True)
    print('   DRAFT BOARD   '.center( pd.options.display.width, '*'))
    if drop_stats is None:
        drop_stats = []
    if hide_pos is None:
        hide_pos = []
    with pd.option_context('display.max_rows', None):
        print(df[~df.pos.isin(hide_pos)].drop(drop_stats, inplace=False, axis=1).head(ntop))
    if npos > 0:
        positions = [pos for pos in ['QB', 'RB', 'WR', 'TE', 'K', 'DST'] if pos not in hide_pos]
        # print df[df.pos.isin(positions)].groupby('pos')# .agg({'exp_proj':sum}).nlargest(npos) # can't figure out groupby right now -- might tidy up the output
        for pos in positions:
            print(df[df.pos == pos].drop(drop_stats, inplace=False, axis=1).head(npos))

def print_top_position(df, pos, ntop=24, sort_key='vols', sort_asc=False, drop_stats=None):
    """prints the top `ntop` players in the position in dataframe df"""
    if sort_key is None:
        df.sort_index( ascending=sort_asc, inplace=True)
    else:
        df.sort_values( sort_key, ascending=sort_asc, inplace=True)
    if drop_stats is None:
        drop_stats = []
    # drop_cols = ['volb', 'tier']
    if pos.upper() == 'FLEX':
        with pd.option_context('display.max_rows', None):
            print(df.loc[df['pos'].isin(['RB', 'WR', 'TE'])].drop(drop_stats, inplace=False, axis=1).head(ntop))
    else:
        with pd.option_context('display.max_rows', None):
            print(df[df.pos == pos.upper()].drop(drop_stats, inplace=False, axis=1).head(ntop))

def push_to_player_list(index, ap, pp):
    """
    index: index of player to be removed from available
    ap: dataframe of available players
    pp: dataframe of picked players
    """
    if index not in pp.index:
        raise IndexError('The index ({}) does not indicate a picked player!'.format(index))
    player = pp.loc[index]
    if len(ap[ap.index == index]) > 0:
        print('The index of the picked player is already in the available player list.')
        print('Someone needs to clean up the logic...')
        print('DEBUG: picked players w/index:', pp.loc[index])
        print('DEBUG: available players w/index:', ap.loc[index])
    # must use loc, not iloc, since positions may move
    ap.loc[index] = player
    # ap.sort_values('vols', ascending=False, inplace=True) # don't re-sort here
    name = player['player']
    pos = player['pos']
    team = player['team']
    print('replacing {} ({}) - {}'.format(name, team, pos))
    pp.drop(index, inplace=True)

def save_player_list(outname, ap, pp=None):
    """saves the available and picked player sets with label "outname"."""
    print('Saving with label {}.'.format(outname))
    ap.to_csv(outname+'.csv', index=False)
    if pp is not None:
        pp.to_csv(outname+'_picked.csv', index=False)

def simplify_name(name):
    """
    maps "A.J. Smith Jr." to "aj_smith_jr"
    """
    return name.strip().lower().replace(' ', '_').replace('\'', '').replace('.', '')
        
def verify_and_quit():
    user_verify = input('Are you sure you want to quit and lose all progress [y/N]? ')
    if user_verify.strip() == 'y':
        print('Make sure you beat Russell.')
        exit(0)
    elif user_verify.lower().strip() == 'n':
        print('OK then, will not quit after all.')
    else:
        print('Did not recognize confirmation. Will not quit.')

# note that this class can be used with tab-autocompletion...
# can we give it more words in its dictionary? (e.g. player names)
class MainPrompt(Cmd):
    """
    This is the main command loop for the program.
    The important data will mostly be copied into member variables so it has easy access.
    """
    # overriding default member variable
    prompt = ' $$ '

    # member variables to have access to the player dataframes
    ap = pd.DataFrame()
    pp = pd.DataFrame()
    newsdf = None

    _sort_key = 'auction' # 'vbsd' #'vols'
    _sort_asc = False
    
    flex_pos = ['RB', 'WR', 'TE']

    hide_pos = ['K', 'DST']
    hide_stats = ['tier', 'pos', 'volb']

    disabled_pos = ['K', 'DST']

    _known_strategies = ['vols', 'vbsd', 'volb', 'vorp', 'adp', 'ecp']
    
    # member variables for DRAFT MODE !!!
    draft_mode = False
    i_manager_turn = None
    manager_picks = [] # when initialized, looks like [1,2,3,...,11,12,12,11,10,...]
    user_manager = None
    manager_names = {}
    manager_auto_strats = {} # we can set managers to automatically pick using a strategy
    n_teams = None
    n_roster_per_team = {}

    # this is a member function we are overriding
    def emptyline(self):
        """
        do nothing when an empty line is entered.
        (without this definition, the last command is repeated)
        """
        pass

    def precmd(self, line):
        """
        this stub is run before every command is interpreted
        """
        self._update_vorp()
        # we need to return the line so that Cmd.onecmd() can process it
        # if we needed to, we would pre-process the input here
        return line

    def _advance_snake(self):
        """move up one step in the snake draft"""
        self.i_manager_turn = self.i_manager_turn + 1
        if self.i_manager_turn >= len(self.manager_picks):
            print('Draft is over!')
            conf = input('Are you done [y/N]? ')
            if conf != 'y':
                print('Undoing last pick')
                push_to_player_list(self.pp.index[-1], self.ap, self.pp)
                # self._update_vorp()
                return self._regress_snake()
            # self.draft_mode = False # if we do this then we can't call "evaluate all". turning this off might cause other bugs
            i_manager_turn = None
            manager_picks = []
            print('You\'re done! Type `evaluate all` to see a summary for each team.')
            self._set_prompt()
            return
        self._set_prompt()
        #####
        manager = self.manager_picks[self.i_manager_turn]
        if manager in self.manager_auto_strats:
            try:
                pickno = self.i_manager_turn + 1
                self._update_vorp()
                player_index = self._pick_rec(manager, self.manager_auto_strats[manager])
                pop_from_player_list(player_index, self.ap, self.pp,
                                     manager=manager, pickno=pickno)
                self._advance_snake()
            except IndexError as e:
                print(e)
                print('could not pick player from list.')

    def _regress_snake(self):
        """move up one step in the snake draft"""
        self.i_manager_turn = self.i_manager_turn - 1
        self._set_prompt()

    def _get_current_manager(self):
        """returns number of current manager"""
        if not self.manager_picks or self.i_manager_turn is None:
            return None
        if self.i_manager_turn >= len(self.manager_picks):
            return None
        return self.manager_picks[self.i_manager_turn]

    def _get_manager_name(self, num=None):
        """
        returns name of manager number num
        if num is None then uses current manager
        """
        if num is None:
            num = self._get_current_manager()
        return self.manager_names[num] if num in self.manager_names else 'manager {}'.format(num)
        
    def _set_prompt(self):
        manno = self._get_current_manager()
        if manno is not None:
            managername = self._get_manager_name()
            if self.user_manager is not None and manno == self.user_manager:
                self.prompt = ' s~({},{})s~  your pick! $$ '.format(manno, self.i_manager_turn+1)
            else:
                self.prompt = ' s~({},{})s~  {}\'s pick $$ '.format(manno, self.i_manager_turn+1, managername)
        else:
            self.prompt = ' $$ '

    def _get_manager_roster(self, manager, pp=None):
        """returns dataframe of manager's roster"""
        # this will return a small copy w/ the manager index removed
        if pp is None:
            pp = self.pp
        if len(pp) == 0:
            return pp # there isn't anything in here yet, and we haven't added the "manager" branch
        if 'manager' in pp:
            return pp[pp.manager == manager].drop('manager', inplace=False, axis=1)
        else:
            return pp

    def _get_managers_til_next(self):
        """get list of managers before next turn"""
        # first we get the list of managers the will go before our next turn
        if not self.manager_picks:
            print('"managers til next" is only sensible in draft mode.')
            return None
        i_man = self.i_manager_turn
        current_team = self.manager_picks[i_man]
        comp_mans = []
        for man in self.manager_picks[i_man:]:
            if man not in comp_mans:
                comp_mans.append(man)
            else:
                break
        comp_mans.remove(current_team) # don't include our own roster
        return comp_mans

    def _pick_rec(self, manager, strat='vols', ap=None, pp=None, disabled_pos=None, vona_strat='adp'):
        """
        picks the recommended player with the highest strat value 
        returns the index of that player
        """
        # TODO: multiply by "need factor", based on how many of that position you have.
        # e.g. 0.8 once starters are full, 0.6 when already have 1 backup, 0.4 for 2, ...
        if ap is None:
            ap = self.ap
        if pp is None:
            pp = self.pp
        if disabled_pos is None:
            disabled_pos = []
        roster = self._get_manager_roster(manager, pp)
        total_roster_spots = sum([self.n_roster_per_team[pos] for pos in self.n_roster_per_team])
        if len(roster) >= total_roster_spots:
            manname = self._get_manager_name()
            # print '{}\'s roster has no available spots left'.format(manname)
            # return None # don't return, we can still form a recommendation
        
        starting_roster_spots = sum([self.n_roster_per_team[pos]
                                     for pos in self.n_roster_per_team
                                     if pos.upper() is not 'BENCH'])
        crap_positions = ['K', 'DST'] # add DST when (or if) we bother
        # crap_starting_roster_spots = sum([self.n_roster_per_team[pos] for pos in crap_positions])
        needed_crap_starter_positions = [pos for pos in crap_positions
                                         if len(roster[roster.pos == pos])
                                         < self.n_roster_per_team[pos]]
        # key_starting_roster_spots = starting_roster_spots - crap_starting_roster_spots

        key_positions = ['QB', 'RB', 'WR', 'TE'] # this concept includes FLEX so don't count it
        # realistically "nonflex" will just be QBs but let's keep it flexible
        key_nonflex_positions = [pos for pos in key_positions if pos not in self.flex_pos]
        needed_key_starter_positions = []
        needed_key_starter_positions.extend([pos for pos in key_nonflex_positions
                                             if len(roster[roster.pos == pos])
                                             < self.n_roster_per_team[pos]])
        # print [len(roster[roster.pos == pos])
        #        > self.n_roster_per_team[pos] for pos in self.flex_pos]
        used_flex_spot = any([len(roster[roster.pos == pos]) > self.n_roster_per_team[pos]
                                      for pos in self.flex_pos])
        flex_mult = 0 if used_flex_spot else 1
        needed_key_starter_positions.extend([pos for pos in self.flex_pos
                                             if len(roster[roster.pos == pos])
                                             < self.n_roster_per_team[pos]
                                             + flex_mult*self.n_roster_per_team['FLEX']])
        ## TODO: if picking for a flex spot, they should be evaluated by a separate VOLS/VORP for FLEX (?) -- otherwise e.g. TEs get recommended for flex too often

        current_roster_size = len(roster)
        acceptable_positions = []
        if needed_key_starter_positions:
            # if we still need key starters, make sure we grab these first
            acceptable_positions = needed_key_starter_positions
        elif current_roster_size + len(needed_crap_starter_positions) >= starting_roster_spots:
            # note: this logic will fail to fill crap positions if we're ever in a situation where more than one of each is needed
            # need to get a K/DST to fill the end of the lineup
            acceptable_positions = needed_crap_starter_positions
        else:
            # once we have our starting lineup of important positions we can pick for bench value and kickers
            # vorp does a decent job of not picking kickers too quickly,
            # but we do need to keep it from taking more than one.
            acceptable_crap = [pos for pos in crap_positions
                               if len(roster[roster.pos == pos])
                               < self.n_roster_per_team[pos]]
            # we allow backup players, but don't get more than half our bench with any one position
            acceptable_backup = [pos for pos in key_positions
                                 if len(roster[roster.pos == pos])
                                 < self.n_roster_per_team[pos]
                                 + self.n_roster_per_team['BENCH']//2]
            acceptable_positions = acceptable_backup + acceptable_crap
        ## if it's still too kicker-happy we can get more specific by replacing the above:
        # elif current_roster_size < starting_roster_spots - crap_starting_roster_spots:
        #     ## get no more than 1 backup at each position before
        #     needed_backup_positions = [pos for pos in key_positions
        #                                if len(roster[roster.pos == pos])
        #                                < self.n_roster_per_team[pos] + 1]
        #     acceptable_positions = needed_backup_positions if needed_backup_positions else key_positions
        # elif current_roster_size >= starting_roster_spots - crap_starting_roster_spots\
        #      and current_roster_size < starting_roster_spots:
        #     acceptable_positions = [pos for pos in crap_positions
        #                             if len(roster[roster.pos == pos])
        #                             < self.n_roster_per_team[pos]]
        # else:
        #     print 'roster is overfull.'
        #     acceptable_positions = key_positions
        if strat == 'vona':
            pos = self._get_max_vona_in(acceptable_positions, strat=vona_strat, disabled_pos=disabled_pos)
            if pos is None:
                # then the user probably has the next pick as well and we should just pick for value
                print('do you have the next pick? VONA is not well-defined. will return VOLS.')
                strat = 'vols' # vona_strat # this leads to bad recommendations for ADP and ECP
            else:
                # vona_asc = vona_strat in ['adp', 'ecp']
                # topvonapos = ap[ap.pos == pos].sort_values(vona_strat, vona_asc)
                # take our projection over ADP/ECP. 
                topvonapos = ap[ap.pos == pos].sort_values('exp_proj', ascending=False)
                if len(topvonapos) <= 0:
                    print('error: could not get a list of availble position that maximizes VONA.')
                    print('switch to regulat strat?')
                player_index = topvonapos.index[0]
                return player_index
        if strat == 'vorp':
            self._update_vorp(ap, pp) # just make sure we're using the right value, but probably too conservative
        # TODO: VONA-VORP is probably not updating VORP correctly for the propagated player lists
        acceptable_positions = [pos for pos in acceptable_positions if pos not in disabled_pos]
        if len(acceptable_positions) <= 0:
            # if we've ruled out everything else, just pick one of the main positions
            acceptable_positions = key_positions
        asc = strat in ['adp', 'ecp']
        toppicks = ap[ap.pos.isin(acceptable_positions)].sort_values(strat, ascending=asc)
        if len(toppicks) <= 0:
            print('error: no available players in any position in {}'.format(acceptable_positions))
        # player = topstart.iloc[0] # this is the player itself
        player_index = toppicks.index[0]
        return player_index
    
    def _update_vorp(self, ap=None, pp=None):
        """
        updates the VORP values in the available players dataframe
        based on how many players in that position have been picked.
        a replacement for a 1-st round pick comes from the top of the bench,
        while a replacement for a bottom bench player comes from the waivers.
        """
        # return
        ## should maybe cancel this.. it takes time to compute and we have lots of thresholds now
        if ap is None:
            ap = self.ap
        if pp is None:
            pp = self.pp
        # seems to be called more often than is optimal
        # print 'updating VORP' # for checking that this gets called sufficiently
        # positions = [pos for pos in self.n_roster_per_team.keys()
        #              if pos not in ['FLEX', 'BENCH'] and pos not in self.flex_pos]
        positions = [pos for pos in list(self.n_roster_per_team.keys())
                     if pos not in ['FLEX', 'BENCH']]
        
        for pos in positions:
            # also implement: maximum players on roster
            # TODO: implement that max in the WAIV designation as well (elsewhere)
            # maximum probably won't matter for most drafts, so de-prioritize it
            # while your draft is in an hour :E
            
            pos_picked = pp[pp.pos == pos]
            n_pos_picked = len(pos_picked.index)
            n_waiv_picked = len(pos_picked[pos_picked.tier == 'FA'].index)
            # if any managers picked waiver-tier players, then we can shed
            #  the next-worst bench player from our calculations
            # we can still shed all WAIV players since this case raises the value of the threshold
            posdf = ap[ap.pos == pos]
            # pos_draftable = self.ap[(self.ap.pos == pos) & (self.ap.tier != 'FA')]
            pos_draftable = posdf[posdf.tier != 'FA']
            n_pos_draftable = len(pos_draftable.index) - n_waiv_picked
            vorp_baseline = 0
            if n_pos_draftable <= 0:
                # no more "draftable" players -- vorp should be zero for top
                vorp_baseline = ap[ap.pos == pos]['vols'].max()
            else:
                frac_through_bench = n_pos_picked * 1.0 / (n_pos_picked + n_pos_draftable)
                backup_mask = pos_draftable['tier'] == 'BU'
                # we also need to include the worst starter in our list to make it agree with VOLS before any picks are made
                worst_starters = pos_draftable[~backup_mask].sort_values('vols', ascending=True)
                # best_waivers = pos_draftable[backup_mask].sort_values('vols', ascending=False)
                ls_index = None # index of worst starter in position
                # fw_index = None # index of best wavier option in position (assuming ADP)
                if len(worst_starters) > 0:
                    ls_index = worst_starters.index[0]
                # if len(best_waivers) > 0:
                #     fw_index = best_waivers.index[0]
                ls_mask = pos_draftable.index == ls_index
                draftable_mask = backup_mask | ls_mask
                pos_baseline = pos_draftable[draftable_mask]
                n_pos_baseline = len(pos_baseline.index)
                if n_pos_baseline == 0:
                    # this can happen, e.g. with kickers who have no "backup" tier players
                    ap.loc[ap.pos == pos, 'vorp'] = ap['vols']
                    continue
                index = int(frac_through_bench * n_pos_baseline)
                if index >= len(pos_baseline):
                    print('warning: check index here later')
                    index = len(pos_baseline-1)
                vorp_baseline = pos_baseline['vols'].sort_values( ascending=False ).iloc[index]
            ap.loc[ap.pos == pos, 'vorp'] = ap['vols'] - vorp_baseline

    def do_auction(self, _):
        """
        right now, this just spits out some auction information
        """
        print('  available value per team:')
        print(self.ap.groupby('pos')['auction'].sum()/self.n_teams)
        if self.pp.shape[0] > 0:
            print('\n  picked value per team:')
            print(self.pp.groupby('pos')['auction'].sum()/self.n_teams)
            
    def do_disable_pos(self, args):
        """
        disable positions from recommended picks
        """
        for pos in args.split(' '):
            upos = pos.upper()
            if upos in self.disabled_pos:
                print('{} is already disabled.'.format(upos))
                continue
            if upos in ['QB', 'RB', 'WR', 'TE', 'K', 'DST']:
                print('Disabling {} in recommendations.'.format(upos))
                self.disabled_pos.append(upos)
    def complete_disable_pos(self, text, line, begidk, endidx):
        """implements auto-complete for disable_pos function"""
        all_pos = ['QB', 'RB', 'WR', 'TE', 'K', 'DST']
        avail_disable = [pos for pos in all_pos if pos not in self.disabled_pos]
        if text:
            return [name for name in avail_disable
                    if name.startswith(text.upper())]
        else:
            return avail_disable

    def do_enable_pos(self, args):
        """
        enable positions from recommended picks
        """
        for pos in args.split(' '):
            upos = pos.upper()
            if upos in self.disabled_pos:
                print('Enabling {} in recommendations.'.format(upos))
                self.disabled_pos.remove(upos)
    def complete_enable_pos(self, text, line, begidk, endidx):
        """implements auto-complete for enable_pos function"""
        if text:
            return [name for name in self.disabled_pos
                    if name.startswith(text.upper())]
        else:
            return self.disabled_pos

    def do_evaluate(self, args):
        """
        usage: evaluate [MAN]...
        evaluate one or more rosters
        if no argument is provided, the current manager's roster is shown
        type `evaluate all` to evaluate rosters of all managers
        """
        outfile = open('draft_evaluation.txt', 'w')
        if 'manager' not in self.pp:
            print('roster from selected players:', file=outfile)
            evaluate_roster(self.pp,
                            self.n_roster_per_team,
                            self.flex_pos,
                            outfile=outfile)
            return
        indices = []
        if not args:
            indices = [self._get_current_manager()]
        elif args.lower() == 'all':
            indices = list(range(1,self.n_teams+1))
        else:
            try:
                indices = [int(a) for a in args.split(' ')]
            except ValueError as e:
                print('Could not interpret managers to evaluate.')
                print(e)
        manager_vals = {}
        for i in indices:
            print('{}\'s roster:'.format(self._get_manager_name(i)), file=outfile)
            stval, benchval = evaluate_roster(self._get_manager_roster(i),
                                              self.n_roster_per_team,
                                              self.flex_pos, outfile=outfile)
            manager_vals[i] = stval + benchval

        if len(indices) > 3:
            k = int(np.ceil(np.sqrt(1 + len(indices)))) + 2
            totvals = np.array(list(manager_vals.values()))        
            partitions = get_k_partition_boundaries(totvals, k-1)[::-1]
            tier = 0
            sorted_manager_vals = sorted(list(manager_vals.items()), key=lambda tup: tup[1], reverse=True)
            while len(sorted_manager_vals) > 0:
                tier = tier + 1
                print('Tier {}:'.format(tier), file=outfile)
                part_bound = partitions[0] if len(partitions) > 0 else -np.inf
                tiermans = [y for y in takewhile(lambda x: x[1] > part_bound, sorted_manager_vals)]
                for manager,manval in tiermans:
                    print('  {}: \t{}'.format(self._get_manager_name(manager), int(manval)), file=outfile)
                    # print('  {}'.format(self._get_manager_name(manager)), file=outfile)
                print('\n', file=outfile)
                sorted_manager_vals = sorted_manager_vals[len(tiermans):]
                partitions = partitions[1:]
        if outfile is not None:
            print('evaltuation saved to {}.'.format(outfile.name))
            outfile.close()
            
    def do_exit(self, args):
        """alias for `quit`"""
        self.do_quit(args)

    def do_find(self, args):
        """
        usage: find NAME...
        finds and prints players with the string(s) NAME in their name.
        """
        # search_words = [word for word in args.replace('_', ' ').split(' ') if word]
        search_str = args.replace('_', ' ')
        find_player(search_str, self.ap, self.pp)
    def complete_find(self, text, line, begidk, endidx):
        """implements auto-complete for player names"""
        avail_names = pd.concat([self.ap, self.pp], sort=False)['player']
        mod_avail_names = [simplify_name(name)
                           for name in avail_names]
        if text:
            return [name for name in mod_avail_names
                    if name.startswith(text.lower())]
        else:
            return mod_avail_names

    def do_handcuff(self, args):
        """
        usage: handcuff I...
        find potential handcuffs for player with index(ces) I
        """
        index = None
        try:
            index = int(args)
        except ValueError as e:
            all_players = pd.concat([self.ap, self.pp], sort=False)
            # criterion = all_players['player'].map(lambda n: args.lower().replace('_', ' ') in n.lower().replace('\'', ''))
            criterion = all_players['player'].map(simplify_name).str.contains(simplify_name(args))
            filtered = all_players[criterion]
            if len(filtered) <= 0:
                print('Could not find available player with name {}.'.format(args))
                return
            if len(filtered) > 1:
                print('Found multiple players:')
                print(filtered.drop(self.hide_stats, axis=1))
                return
            assert(len(filtered) == 1)
            index = filtered.index[0]
        find_handcuff(index, self.ap, self.pp)
    def complete_handcuff(self, text, line, begidk, endidx):
        """implements auto-complete for player names"""
        # avail_names = pd.concat([self.ap, self.pp])['player']
        avail_names = pd.concat([self.ap['player'], self.pp['player']], sort=False).map(simplify_name)
        if text:
            return [name for name in avail_names
                    if name.startswith(simplify_name(text))]
        else:
            return avail_names

    def do_hide(self, args):
        """
        usage: hide ITEM...
        hide a position or statistic from view
        """
        for arg in args.split(' '):
            if arg.lower() in self.ap:
                if arg.lower() not in self.hide_stats:
                    print('Hiding {}.'.format(arg.lower()))
                    self.hide_stats.append(arg.lower())
                else:
                    print('{} is already hidden.'.format(arg))
            elif arg.upper() in ['QB', 'RB', 'WR', 'TE', 'K', 'DST']:
                print('Hiding {}s.'.format(arg.upper()))
                self.hide_pos.append(arg.upper())
            else:
                print('Could not interpret command to hide {}.'.format(arg))
                print('Available options are in:')
                print('QB, RB, WR, TE, K, DST')
                print(self.ap.columns)
    def complete_hide(self, text, line, begidk, endidx):
        """implements auto-complete for hide function"""
        all_pos = ['QB', 'RB', 'WR', 'TE', 'K', 'DST']
        avail_hide_pos = [pos for pos in all_pos if pos not in self.hide_pos]
        avail_hide_stat = [stat for stat in self.ap.columns if stat not in self.hide_stats]
        avail_hides = avail_hide_pos + avail_hide_stat
        if text:
            return [name.lower() for name in avail_hides
                    if name.startswith(text.lower())]
        else:
            return [name.lower() for name in avail_hides]

    def do_info(self, args):
        """print full data and news about player"""
        criterion = self.ap['player'].map(simplify_name).str.contains(simplify_name(args))
        filtered = self.ap[criterion]
        if len(filtered) <= 0:
            print('Could not find available player with name {}.'.format(args))
            return
        if len(filtered) > 1:
            print('Found multiple players:')
            print(filtered.drop(self.hide_stats, axis=1))
            return
        assert(len(filtered) == 1)
        pl = filtered.iloc[0]
        print()
        for data in pl.items():
            out = '{}:      \t{:.4f}' if type(data[1]) is np.float64 else '{}:      \t{}'
            print(out.format(*data))
        if pl['n'] == '*' and self.newsdf is not None:
            # then there is a news story that we need the details of
            newsnames = self.newsdf.player.map(simplify_name)
            pix = (newsnames.isin(get_close_matches(simplify_name(pl.player),
                                                    newsnames.values)))
            if newsnames[pix].shape[0] != 1:
                logging.error('did not unambiguously identify news item')
            for _,nrow in self.newsdf[pix].iterrows():
                print('\n  {}: {}'.format(nrow.player, nrow.details))
        print()
    def complete_info(self, text, line, begidk, endidx):
        """implements auto-complete for player names"""
        names = pd.concat((self.ap.player, self.pp.player)).map(simplify_name)
        return [name for name in names
                if name.startswith(text.lower())] if text else names
        
    def do_list(self, args):
        """alias for `ls`"""
        self.do_ls(args)

    def do_load(self, args):
        """
        usage load [OUTPUT]
        loads player lists from OUTPUT.csv (default OUTPUT is draft_players)
        """
        print('if you quit from draft mode, then that state will not be saved.')
        print('in principle this can be extracted from the manager of the picked players,')
        print('but that is not yet implemented.')
        outname = args if args else 'draft_backup'
        self.ap, self.pp = load_player_list(outname)

    def do_ls(self, args):
        """
        usage: ls [N [M]]
        prints N top available players and M top players at each position
        """
        spl_args = [w for w in args.split(' ') if w]
        ntop, npos = 16, 3
        try:
            if spl_args:
                ntop = int(spl_args[0])
            if spl_args[1:]:
                npos = int(spl_args[1])
        except ValueError as e:
            print('`ls` requires integer arguments.')
            print(e)
        print_top_choices(self.ap, ntop, npos, self._sort_key, self._sort_asc, self.hide_stats, self.hide_pos)

    def do_lspick(self, args):
        """prints summary of players that have already been picked"""
        # we already have the `roster` command to look at a roster of a manager;
        # TODO: let optional argument select by e.g. position?
        print_picked_players(self.pp, self.ap)

    def do_lspos(self, args):
        """
        usage: lspos POS [N]
        prints N top available players at POS where POS is one of (qb|rb|wr|te|flex|k)
        """
        spl_args = [w for w in args.split(' ') if w]
        if not spl_args:
            print('Provide a position (qb|rb|wr|te|flex|k) to the `lspos` command.')
            return
        pos = spl_args[0]
        ntop = 16
        if spl_args[1:]:
            try:
                ntop = int(spl_args[1])
            except ValueError:
                print('`lspos` requires an integer second argument.')
        print_top_position(self.ap, pos, ntop, self._sort_key, self._sort_asc, self.hide_stats)

    def do_name(self, args):
        """
        name [N] <manager name>
        names the current manager if first argument is not an integer
        """
        if not args:
            logging.error('usage: name [N] <manager name>')
            logging.info('names the current manager if first argument is not an integer')
            return
        mannum = self._get_current_manager()
        splitargs = args.split(' ')
        firstarg = splitargs[0]
        manname = args
        try:
            mannum = int(firstarg)
            manname = ' '.join(splitargs[1:])
        except ValueError as e:
            if mannum is None:
                print(e)
                print('Could not figure out a valid manager to assign name to.')
                print('If not in draft mode, enter a number manually.')
        self.manager_names[mannum] = manname
        self._set_prompt()

    def do_next_managers(self, _):
        """
        usage: next_managers
        prints the remaining open starting lineup positions of the managers
        that will have (two) picks before this one's next turn.
        """
        if not self.draft_mode:
            print('this command is only available in draft mode.')
            return
        comp_mans = self._get_managers_til_next()
        
        # here we loop through the managers and see how many starting spots they have
        starting_pos = [pos for pos,numpos in list(self.n_roster_per_team.items())
                        if numpos > 0 and pos not in ['FLEX', 'BENCH']]
        pos_totals = {key:0 for key in starting_pos}
        pos_totals['FLEX'] = 0
        for man in comp_mans:
            print('{} needs starters at:'.format(self._get_manager_name(man)))
            roster = self._get_manager_roster(man)
            for pos in starting_pos:
                hasleft = self.n_roster_per_team[pos] - len(roster[roster.pos == pos])
                if hasleft > 0:
                    print('{}: {}'.format(pos, hasleft))
                    pos_totals[pos] = pos_totals[pos] + hasleft
            flexused = sum([max(0, len(roster[roster.pos == pos])
                            - self.n_roster_per_team[pos])
                            for pos in self.flex_pos])
            flexleft = self.n_roster_per_team['FLEX'] - flexused
            if flexleft > 0:
                print('FLEX: {}'.format(flexleft))
                pos_totals['FLEX'] = pos_totals['FLEX'] + flexleft
        if sum(pos_totals.values()) > 0:
            print('\ntotal open starting roster spots ({} picks):'.format(2*len(comp_mans)))
            for pos,n in list(pos_totals.items()):
                if n > 0:
                    print('{}: {}'.format(pos, n))

    def do_pick(self, args):
        """
        usage: pick I
               pick <strategy>  (in snake draft mode)
               pick <strategy> auto
        remove player with index I from available player list
        in snake draft mode, `pick vols` can be used to pick the VOLS recommended player.
        if "auto" is provided then this manager will automatically pick following this strategy
        """
        manager = self._get_current_manager()
        index = None
        if manager is not None:
            argl = args.lower().split(' ')
            if argl and argl[0] in self._known_strategies:
                index = self._pick_rec(manager, argl[0])
            if len(argl) > 1 and argl[1] == 'auto':
                self.manager_auto_strats[manager] = argl[0]
        elif args.lower().split(' ')[0] in self._known_strategies:
            print('Must be in draft mode to set an automatic strategy.')
        try:
            if index is None:
                index = int(args) 
        except ValueError as e:
            # criterion = self.ap['player'].map(lambda n: simplify_name(args) in n.lower().replace('\'', ''))
            criterion = self.ap['player'].map(simplify_name).str.contains(simplify_name(args))
            filtered = self.ap[criterion]
            if len(filtered) <= 0:
                print('Could not find available player with name {}.'.format(args))
                return
            if len(filtered) > 1:
                print('Found multiple players:')
                print(filtered.drop(self.hide_stats, axis=1))
                return
            assert(len(filtered) == 1)
            index = filtered.index[0]
        try:
            pickno = self.i_manager_turn + 1 if self.draft_mode else None
            pop_from_player_list(index, self.ap, self.pp, manager=manager, pickno=pickno)
            self._update_vorp()
            if self.draft_mode:
                self._advance_snake()
        except IndexError as e:
            print(e)
            print('could not pick player from list.')
    def complete_pick(self, text, line, begidk, endidx):
        """implements auto-complete for player names"""
        avail_names = self.ap['player']
        # TODO: make it look a bit prettier by allowing spaces instead of underscores.
        # see: https://stackoverflow.com/questions/4001708/change-how-python-cmd-module-handles-autocompletion
        # clean up the list a bit, removing ' characters and replacing spaces with underscores
        mod_avail_names = [simplify_name(name)
                           for name in avail_names]
        if text:
            return [name for name in mod_avail_names
                    if name.startswith(text.lower())]
        else:
            return [name for name in mod_avail_names]
    
    def do_plot(self, args):
        """
        plot the dropoff of a quantity by position
        """
        yquant = args.strip().lower() if args else self._sort_key
        if yquant not in self.ap:
            print('{} is not a quantity that can be plotted.'.format(yquant))
            return
        plotdf = self.ap[self.ap.tier != 'FA'][['pos', yquant]] # ?
        for pos in ['QB', 'RB', 'WR', 'TE', 'K', 'DST']:
            pos_idxs = plotdf[plotdf.pos == pos].sort_values(yquant, ascending=False).index
            for rank,idx in enumerate(pos_idxs):
                plotdf.loc[idx,'posrank'] = rank
        g = sns.pointplot(data=plotdf, x='posrank', y=yquant,
                           hue='pos',
                           aspect=2.0 # make the plot wider by factor of 2
                           # , kind='strip' # this style looks decent, but I prefer lines connecting these points because they're sorted
        )
        g.set_xlabel('Position rank')
        g.set_ylabel(yquant)
        # g.set_xticklabels(tick_labels.astype(int))
        g.set_xticklabels([]) # by default the tick labels are drawn as floats, making them hard to read
        plt.show()
        
    def do_q(self, args):
        """alias for `quit`"""
        self.do_quit(args)

    def do_quit(self, _):
        """
        exit the program
        """
        verify_and_quit()

    def do_recommend(self, args):
        """print recommendations"""
        manager = self._get_current_manager()
        for strat in self._known_strategies:
            pick = self._pick_rec(manager, strat, disabled_pos=self.disabled_pos)
            player = self.ap.loc[pick]
            print(' {} recommended:\t{}   {} ({}) - {}'.format(strat.upper(), pick, player['player'], player.team, player.pos))
        if self.manager_picks:
            vona_strats = ['vols', 'volb', 'vbsd', 'adp', 'ecp']
            # vona-vorp takes too long
            for strat in vona_strats:
                pick = self._pick_rec(manager, strat='vona', vona_strat=strat, disabled_pos=self.disabled_pos)
                player = self.ap.loc[pick]
                print(' VONA-{} recommended:\t{}   {} ({}) - {}'.format(strat.upper(), pick, player['player'], player.team, player.pos))
                
    def do_roster(self, args):
        """
        usage: roster [N]...
               roster all
        prints the roster of the current manager so far
        can take a number or series of numbers to print only those manager's
        if "all" is passed then it will output all rosters
        """
        # this function is pretty redundant with the `evaluate` command, which give more detailed information (e.g. breaking up starters and bench)
        if not self.draft_mode:
            print('The `roster` command is only available in draft mode.')
            return
        if args.lower() == 'all':
            for i_man in range(1, 1+self.n_teams):
                manname = self._get_manager_name(i_man)
                print('\n {}:'.format(manname))
                theroster = self._get_manager_roster(i_man)
                if len(theroster) > 0:
                    print(theroster.drop(self.hide_stats, axis=1))
                else:
                    print('No players on this team yet.\n')
            print()
            return
        if not args:
            print('\n {}:'.format( self._get_manager_name() ))
            theroster = self._get_manager_roster(self._get_current_manager())
            if len(theroster) > 0:
                print(theroster.drop(self.hide_stats, axis=1))
                print()
            else:
                print('No players on this team yet.\n')
            return
        indices = []
        try:
            indices = [int(i) for i in args.split(' ')]
        except ValueError as e:
            print('`roster` requires integer arguments')
            print(e)
            return
        for i in indices:
            manname = self._get_manager_name(i)
            print('\n {}:'.format(manname))
            theroster = self._get_manager_roster(i)
            if len(theroster) > 0:
                print(theroster)
            else:
                print('No players on this team yet.')
        print() # newline

    def do_save(self, args):
        """
        usage: save [OUTPUT]
        saves player lists to OUTPUT.csv (default OUTPUT is draft_players)
        """
        outname = args if args else 'draft_players'
        save_player_list(outname, self.ap, self.pp)

    def do_show(self, args):
        """
        usage: show ITEM...
        show a position or statistic that has been hidden
        """
        if args.strip().lower() == 'all':
            print('Showing all.')
            self.hide_stats = []
            self.hide_pos = []
            return
        for arg in args.split(' '):
            if arg.lower() in self.hide_stats:
                print('Showing {}.'.format(arg.lower()))
                self.hide_stats.remove(arg.lower())
            elif arg.upper() in self.hide_pos:
                print('Showing {}.'.format(arg.upper()))
                self.hide_pos.remove(arg.upper())
            else:
                print('Could not interpret command to show {}.'.format(arg))
                print('Available options are in:')
                print(self.hide_stats)
                print(self.hide_pos)
    def complete_show(self, text, line, begidk, endidx):
        """implements auto-complete for show function"""
        avail_shows = [name.lower() for name in self.hide_pos + self.hide_stats]
        if text:
            return [name for name in avail_shows
                    if name.startswith(text.lower())]
        else:
            return [name for name in avail_shows]

    def do_snake(self, args):
        """
        usage: snake [N] [strat]
        initiate snake draft mode, with the user in draft position N and all other managers automatically draft with "strat" strategy
        """
        # self._update_vorp() # called in precmd() now
        if self.draft_mode:
            print('You are already in draft mode!')
            return
        if len(self.pp) > 0:
            print('There are already picked players. This is not starting a draft from scratch.')
            print('It is recommended that you quit and start fresh. Draft command will be canceled.')
            return
        numprompt = 'Enter your position in the snake draft [1,...,{}]: '.format(self.n_teams)
        argstr = args.split(' ') if args else [input(numprompt)]
        numstr = argstr[0]
        # TODO: low priority: we could allow for multiple users
        try:
            self.user_manager = int(numstr)
            if self.user_manager not in list(range(1,self.n_teams+1)):
                raise ValueError('Argument not in range.')
        except ValueError as e:
            print(e)
            print('Could not cast argument to draft.')
            print('Use a single number from 1 to {}'.format(self.n_teams))        
            return

        if argstr[1:]:
            strats = [s.lower() for s in argstr[1:] if s.lower() in self._known_strategies]
            for manager in [man for man in range(1,self.n_teams+1)
                            if man != self.user_manager]:
                manstrat = random.choice(strats)
                print('Setting manager {} to use {} strategy.'.format(manager, manstrat))
                self.manager_auto_strats[manager] = manstrat
        
        # perhaps there is a proxy we can use for this to reduce the number of variables
        self.draft_mode = True
        n_rounds = sum([self.n_roster_per_team[pos] for pos in self.n_roster_per_team])
        print('{} rounds of drafting commencing.'.format(n_rounds))
        self.manager_picks = []
        for i in range(n_rounds):
            if i % 2 == 0:
                self.manager_picks.extend(list(range(1,self.n_teams+1)))
            else:
                self.manager_picks.extend(list(range(self.n_teams,0,-1)))
        self.i_manager_turn = -1
        self._advance_snake()

    def do_sort(self, args):
        """
        usage: sort [QUANT]
        choose a stat to sort by
        if QUANT is not provided, sorts by index (label)
        """
        argl = args.lower().strip()
        if not argl:
            argl = None
        if argl is not None and argl not in self.ap:
            print('argl is not a valid sortable quantity.')
            return
        self._sort_key = argl
        self._sort_asc = argl in [None, 'adp', 'ecp']
        # TODO: reset the index here? <- will maybe cause problems after players are drafted
        
    def complete_sort(self, text, line, begidk, endidx):
        """implements auto-complete for sort function"""
        avail_sorts = [name.lower() for name in self.ap.columns]
        if text:
            return [name for name in avail_sorts
                    if name.startswith(text.lower())]
        else:
            return [name for name in avail_sorts]
        

    def do_team(self, args):
        """
        usage: team [TEAM]
        lists players by TEAM abbreviation.
        if no argument is provided, lists the available teams.
        """
        if args:
            find_by_team(args, self.ap, self.pp)
        else:
            print_teams(self.ap, self.pp)

    def do_unpick(self, args):
        """
        usage: unpick
        moves player(s) with index(ces) I from picked list to available.
        if no index is provided, then the last player picked will be returned.
        """
        # we used to allow indices, and multiple indices, but this gets too complicated w/ draft mode.
        if len(self.pp) > 0:
            lasti = self.pp.index[-1]
            try:
                push_to_player_list(lasti, self.ap, self.pp)
                self._update_vorp()
                if self.draft_mode:
                    self._regress_snake()
            except IndexError as e:
                print(e)
                print('could not put player ({}) back in available list.'.format(lasti))
        else:
            print('No players have been picked.')

    # anticipating non-trivialities in the autocomplete here as well, we will simply disable this alias.
    # def do_unpop(self, args):
    #     """alias for unpick"""
    #     self.do_unpick(args)

    # define this here for ease and move it later
    def _step_vona(self, ap, pp,
                  managers_til_next, strat='adp'):
        if not managers_til_next:
            return (ap, pp)
        manager = managers_til_next[0]
        pickidx = self._pick_rec(manager, strat=strat, ap=ap, pp=pp, disabled_pos=None)
        newap = ap.drop(pickidx)
        newpp = ap.loc[ap.index == pickidx].copy()
        newpp['manager'] = manager
        newpp = pd.concat([pp, newpp], sort=False)
        return self._step_vona(newap, newpp, managers_til_next[1:], strat)    

    def do_print_vona(self, args):
        """
        usage: print_vona strat
        print out VONA for each position, assuming `strat` strategy for others
        """
        if not self.manager_picks:
            print('command only available in snake draft mode.')
            return
        # strat = args.strip().lower() if args else None
        for strat in self._known_strategies:
            print('Assuming {} strategy:'.format(strat.upper()))
            positions = [pos for (pos,numpos) in list(self.n_roster_per_team.items())
                         if pos not in ['FLEX', 'BENCH'] and numpos > 0]
            for pos in positions:
                topval = self.ap[self.ap.pos == pos]['exp_proj'].max()
                # get "next available" assuming other managers use strategy "strat" to pick
                managers = self._get_managers_til_next()
                managers.extend(managers[::-1])
                na_ap, na_pp = self._step_vona(self.ap, self.pp, managers, strat)
                naval = na_ap[na_ap.pos == pos]['exp_proj'].max()
                print('{}: {}'.format(pos,topval-naval))
    def _get_max_vona_in(self, positions, strat, disabled_pos=None):
        # vona_dict = {pos:0 for pos in positions)
        if disabled_pos is None:
            disabled_pos = []
        max_vona = -1000.0
        max_vona_pos = None
        for pos in positions:
            topval = self.ap[self.ap.pos == pos]['exp_proj'].max()
            # get "next available" assuming other managers use strategy "strat" to pick
            managers = self._get_managers_til_next()
            managers.extend(managers[::-1])
            na_ap, _ = self._step_vona(self.ap, self.pp, managers, strat)
            naval = na_ap[na_ap.pos == pos]['exp_proj'].max()
            vona = topval - naval
            if vona > max_vona and pos not in disabled_pos:
                max_vona, max_vona_pos = vona, pos
        return max_vona_pos

def main():
    """main function that runs upon execution"""

    # default log level is warning
    logging.getLogger().setLevel(logging.INFO)
    
    ## use argument parser
    parser = argparse.ArgumentParser(description='Script to aid in real-time fantasy draft')
    parser.add_argument('--ruleset', type=str, choices=['phys', 'dude', 'bro', 'nycfc', 'ram'], default='ram',
                        help='which ruleset to use of the leagues I am in')
    parser.add_argument('--n-teams', type=int, default=14, help='number of teams in the league')
    parser.add_argument('--n-qb', type=int, default=1, help='number of starting QBs per team')
    parser.add_argument('--n-rb', type=int, default=2, help='number of starting RBs per team')
    parser.add_argument('--n-wr', type=int, default=2, help='number of starting WRs per team')
    parser.add_argument('--n-te', type=int, default=1, help='number of starting TEs per team')
    parser.add_argument('--n-flex', type=int, default=1, help='number of FLEX spots per team')
    parser.add_argument('--n-dst', type=int, default=1, help='number of D/ST spots per team')
    parser.add_argument('--n-k', type=int, default=1, help='number of starting Ks per team')
    parser.add_argument('--n-bench', type=int, default=7, help='number of bench spots per team')

    args = parser.parse_args()
    n_teams = args.n_teams
    n_roster_per_team = {
        'QB':args.n_qb,
        'RB':args.n_rb,
        'WR':args.n_wr,
        'TE':args.n_te,
        'FLEX':args.n_flex,
        'DST':args.n_dst,
        'K':args.n_k,
        'BENCH':args.n_bench
    }
    n_roster_per_league = {key:(n_teams*val) for key,val in n_roster_per_team.items()}

    # in principle FLEX can be defined in a different way,
    # so we'll leave this definition local so that we might change it later.
    flex_pos = ['RB', 'WR', 'TE']

    # TODO: set this up in its own function in the ruleset file
    if args.ruleset == 'phys':
        rules = phys_league
    if args.ruleset == 'dude':
        rules = dude_league
    if args.ruleset == 'bro':
        rules = bro_league
    if args.ruleset == 'nycfc':
        rules = nycfc_league
    if args.ruleset == 'ram':
        rules = ram_league

    logging.info('Initializing with ruleset:')
    # print some output to verify the ruleset we are working with
    rulestr = '  {} team, {} PPR'.format(n_teams, rules.ppREC)
    if rules.ppPC != 0 or rules.ppINC != 0:
        rulestr += ', {}/{} PPC/I'.format(rules.ppPC, rules.ppINC)
    logging.info(rulestr)
    rosterstr = ' '
    for pos in ['QB', 'RB', 'WR', 'TE', 'FLEX']: # there's always just 1 DST and K, right?
        nper = n_roster_per_team[pos]
        rosterstr += ' {}{} /'.format(nper, pos)
    logging.info(rosterstr[:-2])
    
    main_positions = ['QB', 'RB', 'WR', 'TE', 'K', 'DST']

    year = 2018
    posdfs = []
    for pos in main_positions:
        filename = 'preseason_rankings/project_fp_{}_pre{}.csv'.format(pos.lower(), year)
        posdf = pd.read_csv(filename)
        ## TODO (low priority): try using a multi-indexed dataframe instead of decorating every entry with the position?
        posdf['pos'] = pos
        posdfs.append(posdf)
    # create dataframe of all available players
    availdf = pd.concat(posdfs, ignore_index=True, sort=False)

    # add the team acronym to the DST entries for consistency/elegance
    teamlist = availdf[~availdf.team.isnull()]['team'].sort_values().unique()
    availdf.loc[availdf.pos == 'DST','team'] = availdf.loc[availdf.pos == 'DST','player'].map(lambda n: get_team_abbrev(n, teamlist))

    # if they have no stats listed (NaN) we can treat that as a zero
    # this should be called before ADP is added, since it has some missing values that we want to keep as NaN for clarity
    availdf.fillna(0, inplace=True)

    # get ECP/ADP
    dpfname = 'preseason_rankings/ecp_adp_fp_pre{}.csv'.format(year)
    dpdf = pd.read_csv(dpfname)
    # add team acronym on ECP/ADP data too, so that we can use "team" as an additional merge key
    # dpdf.drop(columns=['rank', 'WSID'],inplace=True)
    dpdf = dpdf[~dpdf.pos.str.contains('TOL')]
    dpdf.loc[dpdf.team.isnull(),'team'] = dpdf.loc[dpdf.team.isnull(),'player'].map(lambda n: get_team_abbrev(n, teamlist))

    # print dpdf
    # only merge with the columns we are interested in for now.
    # combine on both name and team because there are sometimes multiple players w/ same name
    availdf = availdf.merge(dpdf[['player', 'team', 'ecp', 'adp']], how='left', on=['player','team'])
    availdf.loc[:,'n'] = ''
    
    # decorate the dataframe with projections for our ruleset
    # use 15/16 for bye factor, since we're only considering 16 weeks of the season (w/ 1 bye)
    availdf.loc[availdf.pos != 'DST', 'exp_proj'] = get_points(rules, availdf)
    # for DST, just take the FP projection.
    availdf.loc[availdf.pos == 'DST', 'exp_proj'] = availdf['fp_projection']
    # can go ahead and filter out stats once we have projections
    availdf = availdf[['player', 'n', 'team', 'pos', 'adp', 'ecp', 'exp_proj']]    


    ## flag players with news items
    newsdf = pd.read_csv('data/news.csv')
    newsdf = newsdf[newsdf.pos.isin(main_positions)]
    for _,pnews in newsdf.iterrows():
        pnamenews,pteamnews,posnews = pnews[['player', 'team', 'pos']]
        pix = (availdf.pos == posnews)
        pix &= (availdf.player == pnamenews)
        # pix &= (availdf.team == pteamnews) # the team abbreviations are not always uniform #TODO: make it well-defined
        if availdf[pix].shape[0] > 1:
            logging.warning('multiple matches found for news item about {}!'.format(pnamenews))
            print(availdf[pix])
        if availdf[pix].shape[0] == 0:
            pix = (availdf.pos == posnews)
            cutoff = 0.75 # default is 0.6, but this seems too loose
            rmsuff =availdf.player.map(rm_name_suffix) 
            pix &= (rmsuff.isin(get_close_matches(rm_name_suffix(pnamenews),
                                                  rmsuff.values, cutoff=cutoff)))
            if availdf[pix].shape[0] > 1:
                logging.warning('multiple matches found for news item about {}!'.format(pnamenews))
                print(availdf[pix])
            if availdf[pix].shape[0] == 0:
                logging.warning('there is news about {} ({}) {}, but this player could not be found!'.format(pnamenews, pteamnews, posnews))
        availdf.loc[pix,'n'] = '*' # flag this column

    
    availdf.loc[:, 'g'] = 15 # default is 15 games; we'll check for suspensions.
    sussdf = pd.read_csv('data/suspensions.csv')
    rmsuff = availdf.player.map(rm_name_suffix).map(simplify_name).copy()
    for _,psus in sussdf.iterrows():
        pnamesus,pteamsus,possus,gsus = psus[['player', 'team', 'pos', 'games_suspended']]
        pnamesimp = simplify_name(rm_name_suffix(pnamesus))
        pix = (rmsuff == pnamesimp) & (availdf.pos == possus)
        # pix = (availdf.player == pnamesus) & (availdf.pos == possus)
        # pix &= (availdf.team == pteamsus) # the team abbreviations are not always uniform #TODO: make it well-defined
        if len(availdf[pix]) > 1:
            logging.warning('multiple matches found for suspension!')
            print(availdf[pix])
        if len(availdf[pix]) == 0:
            pix = (availdf.pos == posnews)
            cutoff = 0.75 # default is 0.6, but this seems too loose
            pix &= (rmsuff.isin(get_close_matches(pnamesimp,
                                                  rmsuff.values, cutoff=cutoff)[:1]))
            if availdf[pix].shape[0] > 1:
                logging.warning('multiple matches found for suspension of {}!'.format(pnamenews))
                print(availdf[pix])
            if availdf[pix].shape[0] == 0:
                logging.error('Could not find {} ({}) {}, suspended for {} games!'.format(pnamesus, pteamsus, possus, gsus))
        if np.isnan(gsus):
            logging.warning('unknown suspension time for {}'.format(pnamesus))
        else:
            availdf.loc[pix,'g'] = availdf[pix]['g'] - gsus
    
    # players that have been assigned a class so far are starters
    # use this to find the worst value of each starter and subtract it
    #  from the projection to get the "VOLS" (value over last starter).
    # this is just a static calculation right now.
    # in the future we could adjust this for draft position and dynamically
    #  update in the case of other teams making "mistakes".
    gamesdf = availdf[['pos', 'exp_proj', 'g']].copy()
    gamesdf['ppg'] = gamesdf['exp_proj'] / gamesdf['g']
    gamesdf.sort_values('ppg', inplace=True, ascending=False)

    # print (gamesdf)

    ppg_baseline = {}
    games_needed = {pos:(16*n_roster_per_league[pos]) for pos in main_positions}
    games_needed['FLEX'] = 16*n_roster_per_league['FLEX']
    # print(games_needed)

    for index,row in gamesdf.iterrows():
        pos,games = row[['pos', 'g']]
        gneeded = games_needed[pos]
        if gneeded > games:
            games_needed[pos] -= games
        elif 0 < gneeded <= games:
            games_needed[pos] = 0
            ppg_baseline[pos] = row['ppg']
            if pos in flex_pos:
                gleft = games - gneeded
                games_needed['FLEX'] -= gleft
        else:
            assert(gneeded == 0)
            if pos in flex_pos:
                gneeded = games_needed['FLEX']
                if gneeded > games:
                    games_needed['FLEX'] -= games
                elif 0 < gneeded <= games:
                    games_needed['FLEX'] = 0
                    ppg_baseline['FLEX'] = row['ppg']
                # if no games needed, we're done.
            
    # print(ppg_baseline)
    del gamesdf
    del games_needed
            
    for pos in main_positions:
        # the baseline itself should not be subject to too much variance
        # worst_starter_value = starterdf[starterdf.pos == pos]['exp_proj'].min()
        worst_starter_pg = ppg_baseline[pos]
        if pos in flex_pos:
            worst_starter_pg = min(worst_starter_pg, ppg_baseline['FLEX'])
        # the projections are typically for all 16 games, but we only use 15 in fantasy.
        gs = availdf['g']
        # this is more like "total expected value", since it assumes a constant template of worst starter every game
        availdf.loc[availdf.pos == pos, 'vols'] = gs*(availdf['exp_proj']/(gs+1) - worst_starter_pg)
    
    # label nominal (non-flex) starters by their class
    for pos in main_positions:
        # sort the players in each position so we can grab the top indices
        availpos = availdf.loc[availdf.pos == pos, :].sort_values('vols', ascending=False)
        for i_class in range(n_roster_per_team[pos]):
            ia, ib = i_class*n_teams, (i_class+1)*n_teams
            itoppos = availpos.index[ia:ib]
            icls = availdf.index.isin(itoppos)
            availdf.loc[icls, 'tier'] = '{}{}'.format(pos, i_class+1)
    availflex = availdf.loc[(availdf.pos.isin(flex_pos)) & (availdf['tier'].isnull()), :].sort_values('vols', ascending=False)
    for i_class in range(n_roster_per_team['FLEX']):
        ia, ib = i_class*n_teams, (i_class+1)*n_teams
        itoppos = availflex.index[ia:ib]
        icls = availdf.index.isin(itoppos)
        availdf.loc[icls, 'tier'] = 'FLEX{}'.format(i_class+1)

    # now we need to reset the baseline again based on the tiers
    # previously the "vols" value assumes you can pick from any of the top games in the season
    for pos in main_positions:
        worst_starter_season = availdf.loc[(availdf.pos == pos) & (availdf.tier.notnull()), 'vols'].min()
        if pos in flex_pos:
            worst_starter_season_flex = availdf.loc[(availdf.pos.isin(flex_pos) & availdf.tier.notnull()), 'vols'].min()
            worst_starter_season = min(worst_starter_season, worst_starter_season_flex)
        availdf.loc[availdf.pos == pos, 'vols'] = availdf['vols'] - worst_starter_season
        
    
    ## find a baseline based on supply/demand by positions
    # http://www.rotoworld.com/articles/nfl/41100/71/draft-analysis
    ## we will just use average injury by position, instead of accounting for dropoff by rank
    # not dependent on bench size
    
    total_bench_positions = n_roster_per_league['BENCH']
    total_start_positions = len(availdf[availdf.tier.notnull()])
    # print('total start/bench/total positions: {} / {} / {}'.format(total_start_positions, total_bench_positions,
    #                                                                total_start_positions + total_bench_positions))
    prob_play = {pos:(bye_factor * pos_injury_factor[pos] if pos not in ['K', 'DST'] else 1.0) \
                 for pos in main_positions}
    # this is the benchmark for each position to use for the VBSD baseline
    rank_benchmark = {pos:int(np.ceil(
        len(availdf[(availdf.tier.notnull()) & (availdf.pos == pos)])/prob_play[pos]
    )) for pos in main_positions}
    sum_rank_benchmark = sum((val for _,val in rank_benchmark.items()))
    
    for pos in main_positions:
        posdf = availdf[(availdf.pos == pos)].sort_values('vols', ascending=False)
        pos_required = len(posdf[posdf.tier.notnull()]) # number of man-games needed in this position # need this to include flex
        pos_prob_play = prob_play[pos]
        pos_rank_benchmark = rank_benchmark[pos]
        logging.debug('benchmark considered for {}: {}'.format(pos, pos_rank_benchmark))
        pos_benchmark = posdf.head(pos_rank_benchmark)['vols'].min()
        # projections account for bye weeks but not for positional injuries
        availdf.loc[availdf.pos == pos, 'vbsd'] = (availdf['vols'] - pos_benchmark) * pos_injury_factor[pos]
        
        # now we've given the backups a class, the worst projection for each position is the worst bench value.
        # we will define the value minus this to be the VOLB (value over last backup)
        # this a static calculation, and the dynamically-computed VORP might do better.
        # use VOLS and not absolute projection to account for suspensions
        # the bench proportion should be selected
        bench_rank_benchmark = pos_required + (pos_rank_benchmark - pos_required) * total_bench_positions // (sum_rank_benchmark - total_start_positions)
        # nposbench = bench_rank_benchmark - pos_required

        worst_draftable_value = posdf.head(bench_rank_benchmark)['vols'].min()
        # print(worst_draftable_value)
        availdf.loc[availdf.pos == pos, 'volb'] = availdf['vols'] - worst_draftable_value

        # this factor should represent the fraction of games a player at that position and rank should play, outside of injuries which are already accounted for in the vbsd.
        # we'll say players at the baseline have about a 50% chance of starting. it's a pretty arbitrary envelope.
        # it is good to be flat at the top and gradually go to zero,
        # so with the constraint of 50% at the edge this may not be terrible
        # we should really figure out this envelope function w/ simulations, but they need to be more reliable.
        # auction_multiplier = lambda x: max(1/(1 + (x/pos_rank_benchmark)**2), 0.0)
        # auction_multiplier = lambda x: max(1 - 0.5*(x/pos_required)**2, 0.0)
        auction_multiplier = lambda x: max(1/(1 + (x/pos_required)**2), 0.0)
        # K and DST are random and have lots of variance, so manually constrain at the minimum bid.
        if pos in ['K', 'DST']: auction_multiplier = lambda x: 0
            
        # grab this again because now it has vbsd
        # rank by vols and not raw projections to account for suspended players
        posdf = availdf[(availdf.pos == pos)].sort_values('vols', ascending=False)
        for idx in range(posdf.shape[0]):
            label = posdf.index[idx]
            # vols = posdf.iloc[idx]['vols']*pos_prob_play
            vbsd = posdf.iloc[idx]['vbsd'] # this was already multiplied by the injury factor
            volb = posdf.iloc[idx]['volb'] * pos_injury_factor[pos]
            availdf.loc[label,'rank'] = '{}{}'.format(pos, idx+1)
            # volb combined with the envelop above gives reasonable results.
            availdf.loc[label,'auction'] = auction_multiplier(idx)*max(0,np.mean([volb]))
            # availdf.loc[label,'auction'] = auction_multiplier(idx)*max(0,np.mean([vbsd]))
            
    availdf.loc[(availdf.tier.isnull()) & (availdf['volb'] >= 0.), 'tier'] = 'BU'
    # there will be some extra spots since the integer division is not exact. fill these with more flex spots.
    n_more_backups = total_start_positions + total_bench_positions - availdf.tier.count() # count excludes nans
    add_bu_ix = availdf.loc[availdf.tier.isnull()].head(n_more_backups).index
    availdf.loc[add_bu_ix, 'tier'] = 'BU'

    cap = 200 # auction cap per manager
    minbid = 1
    league_cap = n_teams * cap
    # print(league_cap)
    avail_cap = league_cap - minbid * sum((val for pos,val in n_roster_per_league.items()))
    
    total_auction_pts = availdf['auction'].sum() # the unscaled amount of value
    availdf.loc[:,'auction'] *= avail_cap / total_auction_pts
    # availdf.loc[(availdf.auction > 0),'auction'] += minbid
    availdf.loc[~availdf.tier.isnull(),'auction'] += minbid
    
    if not abs(availdf['auction'].sum() - league_cap) < 0.5:
        logging.error('auction totals do not match league cap!')
    
    ## now label remaining players as waiver wire material
    availdf.loc[availdf.tier.isnull(), 'tier'] = 'FA'

    ## finally sort by our stat of choice for display
    sort_stat = 'auction' # 'vbsd'
    availdf = availdf.sort_values(sort_stat, ascending=False)
    availdf.reset_index(drop=True, inplace=True) # will re-number our list to sort by our stat
    
    # make an empty dataframe with these reduces columns to store the picked players
    # this might be better as another level of index in the dataframe, or simply as an additional variable in the dataframe.
    # In the latter case we'd need to explicitly exclude it from print statements.
    pickdf = pd.DataFrame(columns=availdf.columns)

    # set some pandas display options
    pd.options.display.precision = 2 # default is 6
    pd.options.display.width = 108 # default is 80

    # set seaborn style to nice default
    sns.set()

    prompt = MainPrompt()
    prompt.ap = availdf
    prompt.pp = pickdf
    prompt.newsdf = newsdf
    prompt.n_teams = n_teams
    prompt.n_roster_per_team = n_roster_per_team
    try:
        prompt.cmdloop()
    except (SystemExit, KeyboardInterrupt, EOFError):
        # a system exit, Ctrl-C, or Ctrl-D can be treated as a clean exit.
        #  will not create an emergency backup.
        print('Goodbye!')
    except:
        backup_fname = 'draft_backup'
        print('Error: {}'.format(sys.exc_info()))
        print('Backup save with label \"{}\".'.format(backup_fname))
        save_player_list(backup_fname, prompt.ap, prompt.pp)
        raise
        
        
if __name__ == '__main__':
    main()

## vols: value over last starter
## volb: value over last bench (last drafted), assuming ADP
## vomb: value over middle of bench, assuming ADP (removed?)
## vorp: right now, this is a dynamic scale with the baseline moving linearly through player positions as players are drafted in that position. the baseline moves from worst starter to worst bench.
## vbsd: value-based supply/demand : extends starter threshold to further in the bench by a factor accounting for bye weeks and position-dependent injury factors. independent of ADP.
## todo: value over 1.5x starters?
