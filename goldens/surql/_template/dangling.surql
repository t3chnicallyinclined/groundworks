-- map of 7
-- [cites]
RETURN count(SELECT VALUE id FROM cites WHERE record::exists(in) = false OR record::exists(out) = false);
-- [supports]
RETURN count(SELECT VALUE id FROM supports WHERE record::exists(in) = false OR record::exists(out) = false);
-- [undercuts]
RETURN count(SELECT VALUE id FROM undercuts WHERE record::exists(in) = false OR record::exists(out) = false);
-- [attacks]
RETURN count(SELECT VALUE id FROM attacks WHERE record::exists(in) = false OR record::exists(out) = false);
-- [answers]
RETURN count(SELECT VALUE id FROM answers WHERE record::exists(in) = false OR record::exists(out) = false);
-- [bears_on]
RETURN count(SELECT VALUE id FROM bears_on WHERE record::exists(in) = false OR record::exists(out) = false);
-- [relies_on]
RETURN count(SELECT VALUE id FROM relies_on WHERE record::exists(in) = false OR record::exists(out) = false);
