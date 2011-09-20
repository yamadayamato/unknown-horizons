# -*- coding: utf-8 -*-
# ###################################################
# Copyright (C) 2011 The Unknown Horizons Team
# team@unknown-horizons.org
# This file is part of Unknown Horizons.
#
# Unknown Horizons is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the
# Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
# ###################################################
import copy

from horizons.world.production.production import ChangingProduction
from horizons.constants import PRODUCTION, RES
from horizons.scheduler import Scheduler

class UnitProduction(ChangingProduction):
	"""Production, that produces units."""
	def __init__(self, **kwargs):
		super(UnitProduction, self).__init__(auto_start=False, **kwargs)
		self.__init()
		# We have to check manually now after initing because we set auto_start to false
		self._check_inventory()

	def __init(self, progress = 0):
		self.original_prod_line = self._prod_line
		self._prod_line = copy.deepcopy(self._prod_line)
		self.progress = progress # float indicating current production progress

	def _load(self, db, worldid):
		super(UnitProduction, self)._load(db, worldid)
		self.__init()

	## PROTECTED METHODS

	def _give_produced_res(self):
		"""This needs to be overridden as we also have to produce the unit."""
		super(UnitProduction, self)._give_produced_res()

	def _check_available_res(self):
		# Gold must be available from the beginning
		if self._prod_line.consumed_res.has_key(RES.GOLD_ID):
			amount = self._prod_line.consumed_res[RES.GOLD_ID]
			if self.owner_inventory[RES.GOLD_ID] > (-amount): # consumed res have negative sign
				return True
			else:
				return False
		for res in self._prod_line.consumed_res.iterkeys():
			if res is RES.GOLD_ID:
				continue # Gold already checked
			# we change the production, so the amount can become 0
			# in this case, we must no consider this resource, as it has already been fully provided
			if amount != 0 and self.inventory[res] > 0:
				return True
		return False

	def _remove_res_to_expend(self):
		"""Takes as many res as there are and returns sum of amount of res taken."""
		taken = 0
		for res, amount in self._prod_line.consumed_res.iteritems():
			if res == RES.GOLD_ID:
				remnant = self.owner_inventory.alter(res, amount)
				assert remnant == 0, "Not enough gold, shouldn't happend"
				self._prod_line.change_amount(res, remnant)
				taken += amount
			else:
				remnant = self.inventory.alter(res, amount) # try to get all
				self._prod_line.change_amount(res, remnant) # set how much we still need to get
				taken += abs(remnant) + amount
		return taken

	def _produce(self):
		# check if we're done
		still_needed_res = sum(self._prod_line.consumed_res.itervalues())
		if still_needed_res == 0:
			self._finished_producing()
			return

		removed_res = self._remove_res_to_expend()
		# check if there were res
		if removed_res == 0:
			# watch inventory for new res
			self.inventory.add_change_listener(self._check_inventory)
			self._state = PRODUCTION.STATES.waiting_for_res
			self._changed()
			return

		# calculate how much of the whole production process we can produce now
		# and set the scheduler waiting time accordingly (e.g. half of res => wait half of prod time)
		all_needed_res = sum(self.original_prod_line.consumed_res.itervalues())
		part_of_whole_production = float(removed_res) / all_needed_res
		prod_time = Scheduler().get_ticks( part_of_whole_production * self._prod_line.time )
		prod_time = min(prod_time, 1) # wait at least 1 tick
		# do part of production and call this again when done
		Scheduler().add_new_object(self._produce, self, prod_time)

		# set new progress
		self.progress += part_of_whole_production

	def _finished_producing(self, **kwargs):
		super(UnitProduction, self)._finished_producing(continue_producing=False, **kwargs)
		self.state = PRODUCTION.STATES.done
		self.progress = 0
		# reset prodline
		self._prod_line = copy.deepcopy(self.original_prod_line)
