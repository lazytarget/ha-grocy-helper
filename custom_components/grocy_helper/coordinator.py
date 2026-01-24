"""DataUpdateCoordinator for the Grocy-helper component."""

import logging
import traceback
import datetime as dt
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI
from .grocytypes import (
    BarcodeLookup,
    GrocyMasterData,
    GrocyProduct,
    GrocyQuantityUnitConversionResolved,
    GrocyQuantityUnitConversionResult,
    OpenFoodFactsProduct,
)
from .const import OpenFoodFacts

_LOGGER = logging.getLogger(__name__)


class GrocyHelperCoordinator(DataUpdateCoordinator[GrocyMasterData]):
    """Coordinator for updating data from Grocy."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        grocy_api: GrocyAPI,
        barcodebuddy_api: BarcodeBuddyAPI,
        logger: logging.Logger,
        update_interval: dt.timedelta,
    ) -> None:
        """Initialize the Grocy-helper coordinator."""
        super().__init__(
            hass, logger, name="Grocy-helper", update_interval=update_interval
        )
        self.SCAN_INTERVAL = update_interval
        self._config_entry = config_entry
        self._api_grocy: GrocyAPI = grocy_api
        self._api_bbuddy: BarcodeBuddyAPI = barcodebuddy_api
        self._hass = hass
        self._websession = async_get_clientsession(hass)

    async def _async_setup(self) -> None:
        """Initialize coordinator."""
        _LOGGER.info("Init coordinator")

    async def _async_update_data(self) -> GrocyMasterData:
        """Fetch data from Grocy."""
        _LOGGER.info("Update data")
        data = await self.fetch_data()
        return data

    async def fetch_data(self) -> GrocyMasterData:
        """Fetch masterdata from Grocy."""
        try:
            locations = await self._api_grocy.get_locations()
            _LOGGER.debug("Loaded locations: %s", locations)

            shopping_locations = await self._api_grocy.get_shopping_locations()
            _LOGGER.debug("Loaded stores: %s", shopping_locations)

            quantity_units = await self._api_grocy.get_quantityunits()
            _LOGGER.debug("Loaded quantity_units: %s", quantity_units)

            products = await self._api_grocy.get_products()
            _LOGGER.debug("Loaded products: %s", len(products))

            masterdata: GrocyMasterData = {
                "locations": locations,
                "shopping_locations": shopping_locations,
                "quantity_units": quantity_units,
                "products": products,
                "known_qu": {
                    "g": next((qu for qu in quantity_units if qu["name"] == "g"), None),
                    "kg": next(
                        (qu for qu in quantity_units if qu["name"] == "kg"), None
                    ),
                    "ml": next(
                        (qu for qu in quantity_units if qu["name"] == "ml"), None
                    ),
                    "L": next((qu for qu in quantity_units if qu["name"] == "L"), None),
                },
            }
            return masterdata
        except Exception as err:
            _LOGGER.error("Exception when getting data. Err: %s", err)
            _LOGGER.error(traceback.format_exc())
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def lookup_barcode(self, code: str) -> BarcodeLookup:
        product_aliases: list[str] = []
        ica: dict | None = None
        off: OpenFoodFactsProduct | None = None

        # Lookup in ICA integration
        if self._hass.services.has_service("ica", "lookup_product"):
            _LOGGER.debug("Querying ICA for barcode: %s", code)
            r = await self._hass.services.async_call(
                domain="ica",
                service="lookup_product",
                service_data={"identifier": code},
                blocking=True,
                context=None,
                target=None,
                return_response=True,
            )
            _LOGGER.debug("Got ICA response: %s", r)
            # TODO: handle lookup fails (example network issue, auth)
            if r and r.get("success"):
                ica = r.get("data")
        # else:
        #     _LOGGER.warning("Has no ICA lookup service")

        # Lookup in OpenFoodFacts
        off = await self._coordinator.get_product_from_open_food_facts(code)
        # TODO: handle lookup fails (example network issue)
        _LOGGER.info(
            "OpenFoodFacts product: %s",
            self.current_product_openfoodfacts,
        )
        
        ica_output: list[str] = []
        if self.current_product_ica is not None:
            p = self.current_product_ica
            ica_output.append("## ICA provider")
            if b := p.get("ean_name"):
                ica_output.append(f"ean_name: {b}")
                product_aliases.append(b)
            if a := p.get("article"):
                if b := a.get("name"):
                    ica_output.append(f"Article name: **{b}**")
                    product_aliases.append(b)
                if b := a.get("articleId"):
                    ica_output.append(f"Article id: {b}")
                    # TODO: look up more info by articleId?
                if b := a.get("articleGroupId"):
                    ica_output.append(f"ArticleGroupId: {b}")
                    # TODO: map articleGroupId into Grocy Product Group
            if a := p.get("offers"):
                # TODO: add to name suggestion based on Offers
                pass

        off_output: list[str] = []
        if self.current_product_openfoodfacts is not None:
            p = self.current_product_openfoodfacts
            off_output.append("## OpenFoodFacts")
            if b := p.get("product_type"):
                off_output.append(f"Product type: {b}")
            if b := p.get("brand_owner"):
                off_output.append(f"Brand Owner: {b}")
            if b := p.get("brands"):
                off_output.append(f"Brands: {b}")
            if b := p.get("product_name"):
                off_output.append(f"Product name: **{b}**")
                product_aliases.append(b)
            if b := p.get("generic_name"):
                off_output.append(f"Generic name: {b}")
                product_aliases.append(b)

            if b := p.get("product_quantity"):
                u = p.get("product_quantity_unit")
                off_output.append(f"Product Quantity: {b} {u}")
            elif b := p.get("quantity"):
                off_output.append(f"Quantity: {b}")

            if b := p.get("serving_quantity"):
                u = p.get("serving_quantity_unit")
                off_output.append(f"Serving Quantity: {b} {u}")

            if n := p.get("nutriments"):
                if b := n.get("energy_kcal"):
                    off_output.append(f"Energy (per product): {b} kcal")
                if b := n.get("energy_kcal_100g"):
                    off_output.append(f"Energy (per 100): {b} kcal")

            if b := p.get("categories"):
                off_output.append(f"Categories: {b}")

        lookup_output = "\n\n".join(
            ["\n".join(p) for p in (ica_output, off_output) if len(p) > 1]
        )
        lookup_output = f"# Barcode lookup\n\n{lookup_output}"

        # Markdown list
        product_aliases = [f"- {a.strip()}" for a in product_aliases if a]

        result: BarcodeLookup = {
            "barcode": code,
            # "lookup_name": ica_fullname or off_fullname,
            "product_aliases": "\n".join(sorted(set(product_aliases))),
            "lookup_output": lookup_output,
            "product_matches": "\n".join(
                f"{p['name']}" for p in self.matching_products
            ),
        }
        return result

    async def create_product(self, user_input) -> GrocyProduct:
        # argument 'user_input' should instead be 'new_product'?
        # ..let validation and fallback values be a part of Config flow not coordinator?
        new_product: GrocyProduct = {}
        new_product["name"] = user_input["name"]
        new_product["description"] = user_input.get("description")
        new_product["location_id"] = user_input["location_id"]
        new_product["should_not_be_frozen"] = (
            1 if user_input.get("should_not_be_frozen", False) else 0
        )
        # TODO: Remove obsolete validation, that is done in config_flow right now
        # loc = next(
        #     (
        #         loc
        #         for loc in masterdata["locations"]
        #         if str(loc["id"]) == str(new_product["location_id"])
        #     ),
        #     None,
        # )
        # if not loc:
        #     errors["location_id"] = "invalid_location"
        # elif new_product["should_not_be_frozen"] == 1 and loc["is_freezer"] == 1:
        #     errors["location_id"] = "location_is_freezer"

        if val := user_input.get("default_best_before_days"):
            new_product["default_best_before_days"] = int(val)
        if val := user_input.get("default_best_before_days_after_open"):
            new_product["default_best_before_days_after_open"] = int(val)
        new_product["qu_id_purchase"] = user_input.get(
            "qu_id_purchase", user_input.get("qu_id")
        )
        new_product["qu_id_stock"] = user_input.get(
            "qu_id_stock", user_input.get("qu_id")
        )
        new_product["qu_id_price"] = user_input.get(
            "qu_id_price", user_input.get("qu_id")
        )
        new_product["qu_id_consume"] = user_input.get(
            "qu_id_consume", user_input.get("qu_id")
        )
        new_product["row_created_timestamp"] = dt.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if b := user_input.get("parent_product_id"):
            new_product["parent_product_id"] = b
        if b := user_input.get("no_own_stock"):
            new_product["no_own_stock"] = b
        if b := user_input.get("hide_on_stock_overview"):
            new_product["hide_on_stock_overview"] = b
        if b := user_input.get("disable_open"):
            new_product["disable_open"] = b
        if b := user_input.get("cumulate_min_stock_amount_of_sub_products"):
            new_product["cumulate_min_stock_amount_of_sub_products"] = b

        # create product
        _LOGGER.info("user_input: %s", user_input)
        _LOGGER.info("new_product: %s", new_product)
        # if errors:
        #     schema: VolDictType = None
        #     schema = GENERATE_CREATE_PRODUCT_SCHEMA(masterdata, user_input)
        #     schema = vol.Schema(schema)
        #     self.add_suggested_values_to_schema(schema, user_input)
        #     _LOGGER.warning("Input errors: %s", errors)
        #     return self.async_show_form(
        #         step_id=Step.SCAN_ADD_PRODUCT,
        #         data_schema=schema,
        #         errors=errors,
        #     )

        product = await self._api_grocy.add_product(new_product)
        # TODO: check for success!
        _LOGGER.info("created prod: %s", product)
        return product

    
    async def convert_quantity_for_product(
        self,
        product_id,
        from_qu_id,
        to_qu_id,
        amount: float,
    ) -> GrocyQuantityUnitConversionResult | None:
        if from_qu_id == to_qu_id:
            _LOGGER.warning("Trying to resolve quantity conversion for the same unit: %s", from_qu_id)
            # TODO: return GrocyQuantityUnitConversionResult, with strings etc.s
            pass
        
        conversions = (
            await self._api_grocy.resolve_quantity_unit_conversions_for_product_id(
                product_id
            )
        )
        if len(conversions) < 1:
            _LOGGER.error(
                "No conversions could be resolved for the specified product_id: %s",
                product_id,
            )
            return None

        c: Optional[GrocyQuantityUnitConversionResolved] = next(
            (
                conv
                for conv in conversions
                if conv["from_qu_id"] == from_qu_id
                and conv["to_qu_id"] == to_qu_id
                and conv["product_id"] == product_id
            ),
            None,
        )
        if not c:
            _LOGGER.error(
                "Could not resolve a (single) conversion between specified Quantity Units"
            )
            return None
        resolved_amount = amount * float(c["factor"])
        response: GrocyQuantityUnitConversionResult = c.copy()
        response["from_amount"] = amount
        response["to_amount"] = resolved_amount
        return response
        # return {
        #     "product_id": c["product_id"],
        #     "from_qu_id": c["from_qu_id"],
        #     "from_qu_name": c["from_qu_name"],
        #     "from_amount": amount,
        #     "to_qu_id": c["to_qu_id"],
        #     "to_qu_name": c["to_qu_name"],
        #     "to_amount": resolved_amount,
        # }

    async def get_product_from_open_food_facts(
        self,
        code: str,
        fields: Optional[list[str]] = None,
        raise_if_invalid: bool = False,
    ) -> Optional[OpenFoodFactsProduct]:
        """Return a product.

        If the product does not exist, None is returned.

        :param code: barcode of the product
        :param fields: a list of fields to return. If None, all fields are
            returned.
        :param raise_if_invalid: if True, a ValueError is raised if the
            barcode is invalid, defaults to False.
        :return: the API response
        """
        if not code or not isinstance(code, str):
            raise ValueError("code must be a non-empty string")
        url = OpenFoodFacts.APIv2.format(code)
        if fields := fields or OpenFoodFacts.DEFAULT_FIELDS:
            # requests escape comma in URLs, as expected, but openfoodfacts
            # server does not recognize escaped commas.
            # See
            # https://github.com/openfoodfacts/openfoodfacts-server/issues/1607
            url += f"?fields={','.join(fields)}"

        response = await self._websession.get(
            url,
            headers={"User-Agent": "ha-ica-todo"},
            timeout=10,
        )

        try:
            if response.status == 404 and not raise_if_invalid:
                return None
            response.raise_for_status()
        except BaseException as ex:
            _LOGGER.error(
                "Error getting info from OpenFoodFacts. HTTP [GET] Resp: %s -> %s",
                response.status,
                response.text,
            )
            raise ex
        else:
            resp = await response.json()
            if resp is None:
                # product not found
                return None
            if resp.get("status", None) is None:
                raise ValueError(
                    "Seems like the API call to OpenFoodFacts failed. HTTP [GET] Resp: %s -> %s",
                    response.status,
                    response.text,
                )
            if resp["status"] == 0:
                # invalid barcode
                if raise_if_invalid:
                    raise ValueError(f"invalid barcode: {code}")
                return None

            p = resp["product"] if resp is not None else None
            nutriments = p.get("nutriments", {})
            return OpenFoodFactsProduct(
                brand_owner=p.get("brand_owner"),
                brands=p.get("brands"),
                generic_name=p.get("generic_name"),
                product_name=p.get("product_name"),
                product_type=p.get("product_type"),
                product_quantity=p.get("product_quantity"),
                product_quantity_unit=p.get("product_quantity_unit"),
                quantity=p.get("quantity"),
                categories=p.get("categories_hierarchy"),
                categories_hierarchy=p.get("categories_hierarchy"),
                nutriments={
                    "energy_kcal": nutriments.get("energy"),
                    "energy_kcal_100g": nutriments.get(
                        "energy-kcal_100g", nutriments.get("energy-kcal_value")
                    ),
                    "fat_100g": nutriments.get("fat_100g"),
                    "saturated_fat_100g": nutriments.get("saturated-fat_100g"),
                    "carbohydrates_100g": nutriments.get("carbohydrates_100g"),
                    "sugars_100g": nutriments.get("sugars_100g"),
                    "proteins_100g": nutriments.get("proteins_100g"),
                    "salt_100g": nutriments.get("salt_100g"),
                },
            )
