CLMM LP controller configs

Usage (V2 controllers):
- Use the `v2_with_controllers.py` script and set `controllers_config` to include this file.
- Example script config entry:
  controllers_config: ["clmm_lp_uniswap.yml", "clmm_lp_meteora.yml"]

This directory is mounted into `/home/hummingbot/conf/controllers` in the bot container.
