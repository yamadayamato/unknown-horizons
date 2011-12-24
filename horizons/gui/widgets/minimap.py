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

import horizons.main
from fife import fife

from horizons.util import Point, Rect, Circle
from horizons.extscheduler import ExtScheduler
from horizons.util.python.decorators import bind_all
from horizons.util.python import get_counter
from horizons.command.unit import Act

import math
from math import sin, cos

class Minimap(object):
	"""A basic minimap.

	USAGE:
	Minimap can be drawn via GenericRenderer on an arbitrary position (determined by rect in ctor)
	or
	via Pychan Icon. In this case, the rect parameter only determines the size, the
	Minimap will scroll by default on clicks, overwrite on_click if you don't want that.


	NOTE: Rendered images are sorted by name, so use minimap_${X}_foo,
				where X of {a, b, ..} indicating the z-order

	TODO:
	* Remove renderer when used in icon node
	* Clear up distinction of coords where the minimap image or screen is the origin
	* Create a minimap tag for pychan
	** Handle clicks, remove overlay icon
	"""
	COLORS = { "island": (137, 117,  87),
				     "cam":    (  1,   1,   1),
	           "water" : (198, 188, 165),
	           "highlight" : (255, 0, 0), # for events
	           }


	BRANCH_OFFICE_IMAGE = "content/gui/icons/resources/16/placeholder.png"

	SHIP_DOT_UPDATE_INTERVAL = 0.4 # seconds

	RENDER_NAMES = { # alpha-ordering determines the order
	  "background" : "c",
	  "base" : "d", # islands, etc.
	  "branch_office" : "e",
	  "ship" : "f",
	  "cam" : "g",
	  "ship_route" : "h",
	  "highlight" : "l"
	  }

	__minimap_id_counter = get_counter()
	__ship_route_counter = get_counter()
	_instances = [] # all active instances

	def __init__(self, position, session, targetrenderer, imagemanager, renderer=None,
	             cam_border=True, use_rotation=True, on_click=None):
		"""
		@param position: a Rect or a Pychan Icon, where we will draw to
		@param renderer: renderer to be used if position isn't an icon
		@param targetrenderer: target renderer to be used to draw directly into an image
		@param cam_border: boolean, whether to draw the cam border
		@param use_rotation: boolean, whether to use rotation (it must also be enabled in the settings)
		@param on_click: function taking 1 argument or None for scrolling
		"""
		if isinstance(position, Rect):
			self.location = position
			self.renderer = renderer
		else: # assume icon
			self.location = Rect.init_from_topleft_and_size(0, 0, position.width, position.height)
			self.icon = position
			self.use_overlay_icon(self.icon)
		self.session = session
		self.rotation = 0

		if on_click is not None:
			self.on_click = on_click

		self.cam_border = cam_border
		self.use_rotation = use_rotation

		self.world = None
		self.location_center = self.location.center()

		self._id = str(self.__class__.__minimap_id_counter.next()) # internal identifier, used for allocating resources

		self.imagemanager = imagemanager

		self.minimap_image = _MinimapImage(self, targetrenderer)

		#import random
		#ExtScheduler().add_new_object(lambda : self.highlight( (50+random.randint(-50,50), random.randint(-50,50) + 50 )), self, 2, loops=-1)

	def end(self):
		self.disable()
		self.world = None
		self.session = None
		self.renderer = None

	def disable(self):
		"""Due to the way the minimap works, there isn't really a show/hide,
		but you can disable it with this and enable again with draw().
		Stops all updates."""
		ExtScheduler().rem_all_classinst_calls(self)
		if self.session.view.has_change_listener(self.update_cam):
			self.session.view.remove_change_listener(self.update_cam)

		if self in self.__class__._instances:
			self.__class__._instances.remove(self)

	def draw(self):
		"""Recalculates and draws the whole minimap of self.session.world or world.
		The world you specified is reused for every operation until the next draw().
		@param recalculate: do a full recalculation
		"""
		if not self.world:
			self.world = self.session.world # use this from now on
		if not self.world.inited:
			return # don't draw while loading

		self.__class__._instances.append(self)

		# update cam when view updates
		if not self.session.view.has_change_listener(self.update_cam):
			self.session.view.add_change_listener(self.update_cam)

		if not hasattr(self, "icon"): #
			# add to global generic renderer with id specific to this instance
			self.renderer.removeAll("minimap_image"+self._id)
			self.minimap_image.reset()
			node = fife.RendererNode( fife.Point(self.location.center().x, self.location.center().y) )
			self.renderer.addImage("minimap_image"+self._id, node, self.minimap_image.image, False)
		else:
			self.minimap_image.reset()
			self.icon.image = fife.GuiImage( self.minimap_image.image )

		self.update_cam()
		self._recalculate()
		self._timed_update(force=True)

		ExtScheduler().rem_all_classinst_calls(self)
		ExtScheduler().add_new_object(self._timed_update, self, \
								               self.SHIP_DOT_UPDATE_INTERVAL, -1)

	def _get_render_name(self, key):
		return self.RENDER_NAMES[key] + self._id

	def update_cam(self):
		"""Redraw camera border."""
		if not self.cam_border:
			return
		if self.world is None or not self.world.inited:
			return # don't draw while loading
		use_rotation = self._get_rotation_setting()
		self.minimap_image.set_drawing_enabled()
		self.minimap_image.rendertarget.removeAll(self._get_render_name("cam"))
		# draw rect for current screen
		displayed_area = self.session.view.get_displayed_area()
		minimap_corners_as_point = []
		for corner in displayed_area.get_corners():
			# check if the corners are outside of the screen
			corner = list(corner)
			if corner[0] > self.world.max_x:
				corner[0] = self.world.max_x
			if corner[0] < self.world.min_x:
				corner[0] = self.world.min_x
			if corner[1] > self.world.max_y:
				corner[1] = self.world.max_y
			if corner[1] < self.world.min_y:
				corner[1] = self.world.min_y
			corner = tuple(corner)

			coords = self._world_to_minimap( corner, use_rotation )
			minimap_corners_as_point.append( fife.Point(coords[0], coords[1]) )


		for i in xrange(0, 4):
			self.minimap_image.rendertarget.addLine(self._get_render_name("cam"),
			                                        minimap_corners_as_point[i], \
												                      minimap_corners_as_point[ (i+1) % 4],
			                                        *self.COLORS["cam"])

	@classmethod
	def update(cls, tup):
		for minimap in cls._instances:
			minimap._update(tup)

	def _update(self, tup):
		"""Recalculate and redraw minimap for real world coord tup
		@param tup: (x, y)"""
		if self.world is None or not self.world.inited:
			return # don't draw while loading
		minimap_point = self._world_to_minimap( tup, self._get_rotation_setting() )
		world_to_minimap = self._get_world_to_minimap_ratio()
		# TODO: remove this remnant of the old implementation, perhaps by refactoring recalculate()
		minimap_point = (
		  minimap_point[0] + self.location.left,
		  minimap_point[1] + self.location.top,
		)
		rect = Rect.init_from_topleft_and_size(minimap_point[0], minimap_point[1], \
								                           int(round(1/world_to_minimap[0])) + 1, \
								                           int(round(1/world_to_minimap[1])) + 1)
		self._recalculate(rect)

	def use_overlay_icon(self, icon):
		"""Configures icon so that clicks get mapped here.
		The current gui requires, that the minimap is drawn behind an icon."""
		self.overlay_icon = icon
		icon.mapEvents({ \
			icon.name + '/mousePressed' : self._on_click, \
			icon.name + '/mouseDragged' : self._on_drag, \
			icon.name + '/mouseEntered' : self._mouse_entered, \
		  icon.name + '/mouseMoved' : self._mouse_moved,
			icon.name + '/mouseExited' : self._mouse_exited \
		})

	def on_click(self, event, drag):
		"""Handler for clicks (pressed and dragged)
		Scrolls screen to the point, where the cursor points to on the minimap.
		Overwrite this method to your convenience.
		"""
		map_coord = event.map_coord
		moveable_selecteds = [ i for i in self.session.selected_instances if i.movable ]
		if moveable_selecteds and event.getButton() == fife.MouseEvent.RIGHT:
			if drag:
				return
			for i in moveable_selecteds:
				Act(i, *map_coord).execute(self.session)
		elif event.getButton() == fife.MouseEvent.LEFT:
			self.session.view.center(*map_coord)

	def _on_click(self, event):
		event.map_coord = self._get_event_coord(event)
		if event.map_coord:
			self.on_click(event, drag=False)

	def _on_drag(self, event):
		event.map_coord = self._get_event_coord(event)
		if event.map_coord:
			self.on_click(event, drag=True)

	def _get_event_coord(self, event):
		"""Returns position of event as uh map coordinate tuple or None"""
		mouse_position = Point(event.getX(), event.getY())
		if not hasattr(self, "icon"):
			icon_pos = Point(*self.overlay_icon.getAbsolutePos())
			abs_mouse_position = icon_pos + mouse_position
			if not self.location.contains(abs_mouse_position):
				# mouse click was on icon but not actually on minimap
				return
			abs_mouse_position = abs_mouse_position.to_tuple()
		else:
			abs_mouse_position = mouse_position.to_tuple()
		if self._get_rotation_setting():
			abs_mouse_position = self._get_from_rotated_coords(abs_mouse_position)
		return self._minimap_coord_to_world_coord(abs_mouse_position)

	def _mouse_entered(self, event):
		self._show_tooltip(event)

	def _mouse_moved(self, event):
		self._show_tooltip(event)

	def _mouse_exited(self, event):
		if hasattr(self, "icon"): # only supported for icon mode atm
			self.icon.hide_tooltip()

	def _show_tooltip(self, event):
		if hasattr(self, "icon"): # only supported for icon mode atm
			coords = self._get_event_coord(event)
			if not coords: # no valid/relevant event location
				self.icon.hide_tooltip()
				return

			tile = self.session.world.get_tile( Point(*coords) )
			if tile is not None and tile.settlement is not None:
				new_tooltip = tile.settlement.name
				if self.icon.tooltip != new_tooltip:
					self.icon.tooltip = new_tooltip
					self.icon.show_tooltip()
				else:
					self.icon.position_tooltip(event)
			else:
				self.icon.hide_tooltip()

	def highlight(self, tup, factor=1.0, speed=1.0, finish_callback=None, color=(0,0,0)):
		"""Try to get the users attention on a certain point of the minimap.
		@param tuple: world coords
		@param factor: float indicating importance of event
		@param speed: animation speed as factor
		@param finish_callback: executed when animation finishes
		@param color: color of anim, (r,g,b), r,g,b of [0,255]
		@return duration of full animation in seconds"""
		tup = self._world_to_minimap( tup, self._get_rotation_setting())

		# grow the circle from MIN_RAD to MAX_RAD and back with STEPS steps, where the
		# interval between steps is INTERVAL seconds
		MIN_RAD = int( 3 * factor) # pixel
		MAX_RAD = int(12 * factor) # pixel
		STEPS = int(20 * factor)
		INTERVAL = (math.pi / 16) * factor

		def high(i=0):
			i += 1
			render_name = self._get_render_name("highlight")+str(tup)
			self.minimap_image.set_drawing_enabled()
			self.minimap_image.rendertarget.removeAll(render_name)
			if i > STEPS:
				if finish_callback:
					finish_callback()
				return
			part = i # grow bigger
			if i > STEPS/2: # after the first half
				part = STEPS-i  # become smaller

			radius = MIN_RAD + int(( float(part) / (STEPS/2) ) * (MAX_RAD - MIN_RAD) )

			for x, y in Circle( Point(*tup), radius=radius ).get_border_coordinates():
				self.minimap_image.rendertarget.addPoint(render_name, fife.Point(x, y), *color)

			ExtScheduler().add_new_object(lambda : high(i), self, INTERVAL, loops=1)

		high()
		return STEPS*INTERVAL

	def show_unit_path(self, unit):
		"""Show the path a unit is moving along"""
		path = unit.path.path
		if path is None:
			return

		# the path always contains the full path, the unit might be somewhere in it
		position_of_unit_in_path = 0
		unit_pos = unit.position.to_tuple()
		for i in xrange(len(path)):
			if path[i] == unit_pos:
				position_of_unit_in_path = i
				break
		position_of_unit_in_path += 1 # looks nicer when unit is moving
		path = path[position_of_unit_in_path:]

		# draw every step-th coord
		step = 1
		relevant_coords = [ path[0] ]
		for i in xrange(step, len(path), step):
			relevant_coords.append( path[i] )
		relevant_coords.append( path[-1] )

		# get coords, actual drawing
		use_rotation = self._get_rotation_setting()
		self.minimap_image.set_drawing_enabled()
		p = fife.Point(0, 0)
		render_name = self._get_render_name("ship_route") + str(self.__class__.__ship_route_counter.next())
		color = unit.owner.color.to_tuple()
		last_coord = None
		for i in relevant_coords:
			coord = self._world_to_minimap(i, use_rotation)
			if last_coord is not None and \
			   sum( abs(last_coord[i] - coord[i]) for i in (0, 1) ) < 2: # 2 is min dist in pixels
				continue
			last_coord = coord
			p.x = coord[0]
			p.y = coord[1]
			self.minimap_image.rendertarget.addPoint(render_name,
			                                         p, *color)

		def cleanup():
			self.minimap_image.set_drawing_enabled()
			self.minimap_image.rendertarget.removeAll(render_name)

		self.highlight(path[ -1 ], factor=0.4, speed= ( (1.0+math.sqrt(5) / 2) ), finish_callback=cleanup, color=color)

		return True



	def _recalculate(self, where = None):
		"""Calculate which pixel of the minimap should display what and draw it
		@param where: Rect of minimap coords. Defaults to self.location"""
		self.minimap_image.set_drawing_enabled()

		rt = self.minimap_image.rendertarget
		render_name = self._get_render_name("base")

		if where is None:
			where = self.location
			rt.removeAll(render_name)

		# calculate which area of the real map is mapped to which pixel on the minimap
		pixel_per_coord_x, pixel_per_coord_y = self._get_world_to_minimap_ratio()

		# calculate values here so we don't have to do it in the loop
		pixel_per_coord_x_half_as_int = int(pixel_per_coord_x/2)
		pixel_per_coord_y_half_as_int = int(pixel_per_coord_y/2)

		real_map_point = Point(0, 0)
		world_min_x = self.world.min_x
		world_min_y = self.world.min_y
		get_island_tuple = self.world.get_island_tuple
		island_col = self.COLORS["island"]
		location_left = self.location.left
		location_top = self.location.top
		rt_addPoint = rt.addPoint
		fife_point = fife.Point(0,0)

		use_rotation = self._get_rotation_setting()

		# loop through map coordinates, assuming (0, 0) is the origin of the minimap
		# this faciliates calculating the real world coords
		for x in xrange(where.left-self.location.left, where.left+where.width-self.location.left):
			# Optimisation: remember last island
			last_island = None
			island = None
			for y in xrange(where.top-self.location.top, where.top+where.height-self.location.top):

				"""
				This code should be here, but since python can't do inlining, we have to inline
				ourselves for performance reasons
				covered_area = Rect.init_from_topleft_and_size(
				  int(x * pixel_per_coord_x)+world_min_x, \
				  int(y * pixel_per_coord_y)+world_min_y), \
				  int(pixel_per_coord_x), int(pixel_per_coord_y))
				real_map_point = covered_area.center()
				"""
				# use center of the rect that the pixel covers
				real_map_point.x = int(x*pixel_per_coord_x)+world_min_x + \
											pixel_per_coord_x_half_as_int
				real_map_point.y = int(y*pixel_per_coord_y)+world_min_y + \
											pixel_per_coord_y_half_as_int
				real_map_point_tuple = (real_map_point.x, real_map_point.y)

				# check what's at the covered_area
				if last_island is not None and real_map_point_tuple in last_island.ground_map:
					island = last_island
				else:
					island = get_island_tuple(real_map_point_tuple)
				if island is not None:
					last_island = island
					# this pixel is an island
					settlement = island.get_settlement(real_map_point)
					if settlement is None:
						# island without settlement
						color = island_col
					else:
						# pixel belongs to a player
						color = settlement.owner.color.to_tuple()
				else:
					continue

				if use_rotation:
					# inlined _get_rotated_coords
					rot_x, rot_y = self._rotate( (location_left + x, location_top + y), self._rotations)
					fife_point.set(rot_x - location_left, rot_y - location_top)
				else:
					fife_point.set(x, y)

				rt_addPoint(render_name, fife_point, *color)


	def _timed_update(self, force=False):
		"""Regular updates for domains we can't or don't want to keep track of."""
		# update ship dots
		# OPTIMISATION NOTE: there can be pretty many ships, don't rely on the inner loop being rarely executed
		self.minimap_image.set_drawing_enabled()
		self.minimap_image.rendertarget.removeAll(self._get_render_name("ship"))
		use_rotation = self._get_rotation_setting()
		for ship in self.world.ship_map.itervalues():
			if not ship():
				continue

			coord = self._world_to_minimap( ship().position.to_tuple(), use_rotation )

			color = ship().owner.color.to_tuple()
			self.minimap_image.rendertarget.addQuad(self._get_render_name("ship"),
			                                        fife.Point( coord[0]-1, coord[1]-1 ),
			                                        fife.Point( coord[0]-1, coord[1]+2 ),
			                                        fife.Point( coord[0]+2, coord[1]+2 ),
			                                        fife.Point( coord[0]+2, coord[1]-1 ),
			                                        *color)
			if ship() in self.session.selected_instances:
				self.minimap_image.rendertarget.addPoint(self._get_render_name("ship"),
			                                         fife.Point( coord[0], coord[1] ),
			                                         *Minimap.COLORS["water"])
				for x_off, y_off in ((-2,  0),
				                     (+2,  0),
				                     ( 0, -2),
				                     ( 0, +2)):
					self.minimap_image.rendertarget.addPoint(self._get_render_name("ship"),
					                                         fife.Point( coord[0]+x_off, coord[1] + y_off ),
					                                         *color)


		# draw settlement branch offices if something has changed
		settlements = self.world.settlements
		# save only worldids as to not introduce actual coupling
		cur_settlements = set( i.worldid for i in settlements )
		if force or \
		   (not hasattr(self, "_last_settlements") or cur_settlements != self._last_settlements):
			# update necessary
			bo_img = self.imagemanager.load( self.__class__.BRANCH_OFFICE_IMAGE )
			bo_render_name = self._get_render_name("branch_office")
			self.minimap_image.rendertarget.removeAll( bo_render_name )
			# scale bo icons
			ratio = sum(self._get_world_to_minimap_ratio()) / 2.0
			ratio = max(1.0, ratio)
			new_width, new_height = int(bo_img.getWidth()/ratio), int(bo_img.getHeight()/ratio)
			for settlement in settlements:
				coord = settlement.branch_office.position.center().to_tuple()
				coord = self._world_to_minimap(coord, use_rotation)
				point = fife.Point( coord[0], coord[1] )
				self.minimap_image.rendertarget.resizeImage(bo_render_name, point, bo_img,
					                                          new_width, new_height)
			self._last_settlements = cur_settlements


	def rotate_right (self):
		# keep track of rotation at any time, but only apply
		# if it's actually used
		self.rotation -= 1
		self.rotation %= 4
		if self._get_rotation_setting():
			self.draw()

	def rotate_left (self):
		# see above
		self.rotation += 1
		self.rotation %= 4
		if self._get_rotation_setting():
			self.draw()

	## CALC UTILITY
	def _world_to_minimap(self, coord, use_rotation):
		"""Complete coord transformation, batteries included.
		The methods below are for more specialised purposes."""
		coord = self._world_coord_to_minimap_coord( coord )

		if use_rotation:
			coord = self._get_rotated_coords(coord)
		# transform from screen coord to minimap coord
		coord = (
		  coord[0] - self.location.left,
		  coord[1] - self.location.top
		  )

		return coord

	def _get_rotation_setting(self):
		if not self.use_rotation:
			return False
		return horizons.main.fife.get_uh_setting("MinimapRotation")

	_rotations = { 0 : 0,
				         1 : 3 * math.pi / 2,
				         2 : math.pi,
				         3 : math.pi / 2
				         }
	def _get_rotated_coords (self, tup):
		"""Rotates according to current rotation settings.
		Input coord must be relative to screen origin, not minimap origin"""
		return self._rotate(tup, self._rotations)

	_from_rotations = { 0 : 0,
				              1 : math.pi / 2,
				              2 : math.pi,
				              3 : 3 * math.pi / 2
				              }
	def _get_from_rotated_coords (self, tup):
		return self._rotate (tup, self._from_rotations)

	def _rotate (self, tup, rotations):
		rotation = rotations[ self.rotation ]

		x = tup[0]
		y = tup[1]

		# rotate around center of minimap
		x -= self.location_center.x
		y -= self.location_center.y

		new_x = x * cos(rotation) - y * sin(rotation)
		new_y = x * sin(rotation) + y * cos(rotation)

		new_x += self.location_center.x
		new_y += self.location_center.y

		new_x = int(round(new_x))
		new_y = int(round(new_y))

		#some points may get out of range
		new_x = max (self.location.left, new_x)
		new_x = min (self.location.right, new_x)
		new_y = max (self.location.top, new_y)
		new_y = min (self.location.bottom, new_y)

		return (new_x, new_y)

	def _get_world_to_minimap_ratio(self):
		world_height = self.world.map_dimensions.height
		world_width = self.world.map_dimensions.width
		minimap_height = self.location.height
		minimap_width = self.location.width
		pixel_per_coord_x = float(world_width) / minimap_width
		pixel_per_coord_y = float(world_height) / minimap_height
		return (pixel_per_coord_x, pixel_per_coord_y)

	def _world_coord_to_minimap_coord(self, tup):
		"""Calculates which pixel in the minimap contains a coord in the real map.
		@param tup: (x, y) as ints
		@return tuple"""
		pixel_per_coord_x, pixel_per_coord_y = self._get_world_to_minimap_ratio()
		return ( \
			int(round(float(tup[0] - self.world.min_x)/pixel_per_coord_x))+self.location.left, \
			int(round(float(tup[1] - self.world.min_y)/pixel_per_coord_y))+self.location.top \
		)

	def _minimap_coord_to_world_coord(self, tup):
		"""Inverse to _world_coord_to_minimap_coord"""
		pixel_per_coord_x, pixel_per_coord_y = self._get_world_to_minimap_ratio()
		return ( \
			int(round( (tup[0] - self.location.left) * pixel_per_coord_x))+self.world.min_x, \
			int(round( (tup[1] - self.location.top)* pixel_per_coord_y))+self.world.min_y \
		)

	def get_size(self):
		return (self.location.height, self.location.width)

class _MinimapImage(object):
	"""Encapsulates handling of fife Image.
	Provides:
	- self.rendertarget: instance of fife.RenderTarget
	"""
	def __init__(self, minimap, targetrenderer):
		self.minimap = minimap
		self.targetrenderer = targetrenderer
		size = self.minimap.get_size()
		self.image = self.minimap.imagemanager.loadBlank(size[0], size[1])
		self.rendertarget = targetrenderer.createRenderTarget( self.image )
		self.set_drawing_enabled()

	def reset(self):
		"""Reset image to original image"""
		# reload
		self.rendertarget.removeAll()
		size = self.minimap.get_size()
		self.rendertarget.addQuad( self.minimap._get_render_name("background"),
		                           fife.Point(0,0),
		                           fife.Point(0, size[1]),
		                           fife.Point(size[0], size[1]),
		                           fife.Point(size[0], 0),
		                           *Minimap.COLORS["water"])

	def set_drawing_enabled(self):
		"""Always call this."""
		self.targetrenderer.setRenderTarget( self.rendertarget.getTarget().getName(),
		                                     False, 0 )

bind_all(Minimap)
