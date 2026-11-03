return {
	LrSdkVersion = 10.0,
	LrSdkMinimumVersion = 6.0,
	LrToolkitIdentifier = 'br.album.ponte',
	LrPluginName = 'Álbum - Ponte',
	LrPluginInfoUrl = '',

	LrInitPlugin = 'Inicio.lua',
	LrShutdownPlugin = 'Fim.lua',

	LrLibraryMenuItems = {
		{ title = 'Processar pedidos do Álbum', file = 'MenuProcessar.lua' },
		{ title = 'Atualizar lista de presets do Álbum', file = 'MenuPresets.lua' },
	},
	LrExportMenuItems = {
		{ title = 'Processar pedidos do Álbum', file = 'MenuProcessar.lua' },
		{ title = 'Atualizar lista de presets do Álbum', file = 'MenuPresets.lua' },
	},

	VERSION = { major = 1, minor = 0, revision = 0, build = 1 },
}
