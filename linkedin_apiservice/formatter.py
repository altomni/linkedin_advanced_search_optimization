class LinkedInDataFormatter:
    def __init__(self):
        pass

    def date_extract(self, element):
        date_ = str(element.get("year", "-")) + "/" + str(element.get("month", "-"))
        if date_ == "-/-":
            date_ = "current"
        return date_

    def format_duration_in_role(self, duration, in_type):
        years = duration.get("numYears", 0)
        months = duration.get("numMonths", 0)

        year_str = f"{years} year" if years == 1 else f"{years} years"
        month_str = f"{months} month" if months == 1 else f"{months} months"

        if years == 0 and months == 0:
            return ""
        else:
            if years == 0:
                return f"{month_str} in {in_type}"
            elif months == 0:
                return f"{year_str} in {in_type}"
            else:
                return f"{year_str} {month_str} in {in_type}"

    def job_details(self, element):
        return {
            "start_date": self.date_extract(element.get("startedOn", {})),
            "end_date": self.date_extract(element.get("endedOn", {})),
            "title": element.get("title", ""),
            "company_name": element.get("companyName", ""),
            "company_id": element.get("companyUrn", "").replace(
                "urn:li:fs_salesCompany:", ""
            ),
            "description": element.get("description", ""),
        }

    def structure_individual_info(self, individual_info):
        try:
            result = {}

            # Basic profile information
            result["full_name"] = individual_info.get("fullName", "")
            result["first_name"] = individual_info.get("firstName", "")
            result["last_name"] = individual_info.get("lastName", "")

            # Profile URLs and IDs
            entity_urn = individual_info.get("entityUrn", "").replace(
                "urn:li:fs_salesProfile:(", ""
            )[:-1]
            result["profile_url"] = "https://www.linkedin.com/sales/lead/" + entity_urn
            result["profile_id"] = (
                entity_urn.split(",")[0] if "," in entity_urn else entity_urn
            )

            # Current position information
            current_positions = individual_info.get("currentPositions", [])
            past_positions = individual_info.get("pastPositions", [])

            # Current position details
            if current_positions:
                current_position = current_positions[0]
                result["current_title"] = current_position.get("title", "")
                result["current_company"] = current_position.get("companyName", "")

                # Company URL
                try:
                    company_id = current_position.get("companyUrn", "").replace(
                        "urn:li:fs_salesCompany:", ""
                    )
                    result["current_company_url"] = (
                        "https://www.linkedin.com/sales/company/" + company_id
                    )
                    result["current_company_id"] = company_id
                except:
                    result["current_company_url"] = ""
                    result["current_company_id"] = ""

                # Tenure information
                result["tenure_in_role"] = self.format_duration_in_role(
                    current_position.get("tenureAtPosition", {}), "role"
                )
                result["tenure_at_company"] = self.format_duration_in_role(
                    current_position.get("tenureAtCompany", {}), "company"
                )
            else:
                result["current_title"] = ""
                result["current_company"] = ""
                result["current_company_url"] = ""
                result["current_company_id"] = ""
                result["tenure_in_role"] = ""
                result["tenure_at_company"] = ""

            # Location
            result["location"] = individual_info.get("geoRegion", "")

            # Experience history
            result["experience"] = {
                "current": [self.job_details(job) for job in current_positions],
                "past": [self.job_details(job) for job in past_positions],
            }

            # Profile summary
            result["summary"] = individual_info.get("summary", "")

            return result

        except Exception as e:
            print(f"Error in structure_individual_info: {e}")
            return {"error": str(e)}

    def structure_multiple_individuals(self, individuals_list):
        """
        Process multiple LinkedIn profiles and return a list of dictionaries

        Args:
            individuals_list: List of LinkedIn profile data

        Returns:
            List of dictionaries with structured profile information
        """
        return [self.structure_individual_info(profile) for profile in individuals_list]

    def fetch_total_number(self, data):
        return data.get("data", {}).get("metadata", {}).get("totalDisplayCount", "")
