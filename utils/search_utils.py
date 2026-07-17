# import sys
# from pathlib import Path

# # 添加 src 目录到 sys.path (魔法路径)
# # 从当前文件位置: src/utils/search_utils.py -> src/utils/ -> src/
# src_dir = Path(__file__).parent.parent
# sys.path.insert(0, str(src_dir))

import re
from datetime import datetime
import math
from typing import Tuple

import pandas as pd
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import time

from linkedin_integration_service import LinkedInIntegrationService
from linkedin_recruiter_apiservice.api_service import RecruiterService
from utils.general_utils import year_month_diff


load_dotenv()


def expand_search_min_max_years(min_year: int, max_year: int) -> Tuple[int, int]:
    # Calculate search range with adaptive expansion:
    # - Small range (<=5 years): expand more (need wider search net)
    # - Large range (>10 years): expand less (already wide enough)
    range_diff = max(1, abs(max_year - min_year))
    if min_year > max_year:
        tmp_year = max_year
        max_year = min_year
        min_year = tmp_year

    if range_diff <= 3:
        # Very narrow range: expand significantly
        left_expand_ratio = 0.5  # e.g., range=3 -> expand left by 1.5 years
        right_expand_ratio = 0.8  # e.g., range=3 -> expand right by 2.4 years
    elif range_diff <= 5:
        # Narrow range: expand moderately
        left_expand_ratio = 0.4  # e.g., range=5 -> expand left by 2 years
        right_expand_ratio = 0.7  # e.g., range=5 -> expand right by 3.5 years
    elif range_diff <= 8:
        # Medium range: expand less
        left_expand_ratio = 0.2  # e.g., range=8 -> expand left by 1.6 years
        right_expand_ratio = 0.5  # e.g., range=8 -> expand right by 4 years
    else:
        # Large range (>8 years): minimal expansion
        left_expand_ratio = 0.15  # e.g., range=12 -> expand left by 1.8 years
        right_expand_ratio = 0.25  # e.g., range=12 -> expand right by 3 years

    search_min_years = max(0, math.floor(min_year - range_diff * left_expand_ratio))
    search_max_years = min(30, math.ceil(max_year + range_diff * right_expand_ratio))
    return search_min_years, search_max_years


def batch_basic_linkedin_search(
    search_results_num,
    format_filter_conditions,
    job_main_skills,
    records_per_search=25,
    max_search_num=200,
    channel = "recruiter"
):
    final_search_results = []
    max_search_num = min(max_search_num, search_results_num)
    if channel == "recruiter":
        rs = RecruiterService()
        # Ensure format_filter_conditions has the expected structure
        if not format_filter_conditions or not isinstance(format_filter_conditions, dict):
            print(f"[ERROR] batch_basic_linkedin_search: format_filter_conditions is invalid: {format_filter_conditions}")
            return final_search_results
        if "filters" not in format_filter_conditions:
            print(f"[ERROR] batch_basic_linkedin_search: format_filter_conditions missing 'filters' key: {format_filter_conditions.keys()}")
            return final_search_results

        format_filter_conditions["max_pages"] = math.ceil(max_search_num/25)
        print('batch search format_filter_conditions: ', format_filter_conditions)
        t1 = time.time()
        try:
            search_results = rs.get_search_results(format_filter_conditions)
            t2 = time.time()
            print("search time consuming: ", t2 - t1)
            if search_results and "results" in search_results:
                final_search_results.extend(search_results["results"])
                print(f"[batch_basic_linkedin_search] Found {len(search_results['results'])} results")
            else:
                print(f"[batch_basic_linkedin_search] No 'results' key in response: {search_results.keys() if search_results else 'None'}")
        except Exception as e:
            print(f"[ERROR] batch_basic_linkedin_search API call failed: {e}")
            import traceback
            traceback.print_exc()
        return final_search_results
    else:
        raise ValueError("Searching channel name is not allowed: {}".format(channel))


def extract_linkedin_urn(raw_linkedin_id, channel = "recruiter"):
    if channel == "recruiter":
        profile_id = raw_linkedin_id.split(":")[-1]
        if len(profile_id) == 39: return profile_id
        else: return None
    elif channel == "sales_nav":
        # Pattern: Extract ID from format like:
        # urn:li:fs_salesProfile:(ACwAAEEnVB4BkB8zGhLQHdAkb8C144X4aTAkebg,NAME_SEARCH,-uNy)
        m = re.search(
            r"\(([^,]+)", raw_linkedin_id
        )  # capture everything after the first '(' up to the first comma
        profile_id = m.group(1) if m else None
        return profile_id
    else:
        raise ValueError("Searching channel name is not allowed: {}".format(channel))


def extract_candidate_info(job_info, channel="recruiter", include_education_summary=False):
    """
    安全提取候选人信息，处理LinkedIn数据结构的不可预测性

    Args:
        job_info: LinkedIn候选人数据
        channel: 搜索渠道，默认 "recruiter"
        include_education_summary: 是否包含完整教育背景摘要，默认 False（向后兼容）
    """

    if channel == "recruiter":
        try:
            raw_linkedin_id = job_info.get('memberProfile', "")
            if not raw_linkedin_id:
                print("Warning: Missing entityUrn in job_info")
                return None
            linkedin_id = extract_linkedin_urn(raw_linkedin_id, channel="recruiter")
            member_profile = job_info.get('memberProfileResolutionResult', {})
            first_name = member_profile.get("firstName", "Unknown")
            last_name = member_profile.get("lastName", "Unknown")

            # 提取 openToNewOpportunities
            member_preferences = member_profile.get("memberPreferences") or {}
            open_to_opportunities = member_preferences.get("openToNewOpportunities", None)

            # 提取教育背景信息
            educations = member_profile.get("educations", [])
            degree = ("; ").join([_.get('degreeName') for _ in educations if _.get('degreeName', "")])

            # 拼接完整的教育背景摘要（可选）
            education_summary = ""
            if include_education_summary and isinstance(educations, list):
                for edu in educations:
                    if not isinstance(edu, dict):
                        continue

                    degree_name = edu.get('degreeName', 'Unknown Degree')
                    school_name = edu.get('schoolResolutionResult', {}).get('name', 'Unknown School')

                    # 获取时间信息
                    start_date = edu.get('startDateOn', {})
                    end_date = edu.get('endDateOn', {})

                    start_year = start_date.get('year', '') if isinstance(start_date, dict) else ''
                    end_year = end_date.get('year', '') if isinstance(end_date, dict) else ''

                    # 构建教育信息字符串
                    if start_year and end_year:
                        education_summary += f"{degree_name} at {school_name} from {start_year} to {end_year}\n"
                    elif end_year:
                        education_summary += f"{degree_name} at {school_name} (graduated {end_year})\n"
                    else:
                        education_summary += f"{degree_name} at {school_name}\n"

            location = member_profile.get("location", {}).get('displayName', "Unknown")
            person_summary = ""
            current_position = member_profile.get('currentPositions', [])
            past_positions = member_profile.get('workExperience', [])[1:]

            # 🛡️ 确保positions是列表类型
            if not isinstance(past_positions, list):
                past_positions = []
            if not isinstance(current_position, list):
                current_position = []

            # 2. Current position summary
            total_experience_months = 0
            current_position_summary = ""
            all_time_ranges = []  # 用于存储所有职位的时间范围，使用合并区间的方式
            
            if current_position:
                for position in current_position:
                    if not isinstance(position, dict):
                        continue

                    position_company = position.get("companyName", "Unknown Company")
                    company_desc = position.get('companyResolutionResult', {}).get("description", "")
                    position_industry = position.get('companyResolutionResult', {}).get("industries", "Unknown Industry")
                    position_title = position.get("title", "Unknown Title")

                    # 初始化变量
                    year_month_at_company = "Unknown duration"

                    # 处理新的时间格式 {'month': 4, 'year': 1983}
                    try:
                        # 获取开始时间 - 直接使用startDateOn字段
                        started_on = position.get("startDateOn", {})
                        print(f"startDateOn data: {started_on}")
                        
                        # 确保started_on不是None
                        if started_on is None:
                            started_on = {}
                        
                        if isinstance(started_on, dict):
                            start_year = started_on.get("year", 0)
                            start_month = started_on.get("month", 1)  # 如果没有month，默认为1
                            print(f"Parsed: start_year={start_year}, start_month={start_month}")
                            
                            # 如果没有发现year，直接跳过这个position
                            if start_year == 0:
                                print(f"No year found in startDateOn, skipping position at {position_company}")
                                continue
                        else:
                            print(f"startDateOn is not dict: {type(started_on)}, skipping position at {position_company}")
                            continue
                            
                        # 计算到当前时间的总月数
                        from datetime import datetime
                        current_date = datetime.now()
                        current_year = current_date.year
                        current_month = current_date.month
                        
                        # 确保start_year不是None，month不知道默认为1
                        if start_year is None:
                            print(f"Warning: start_year is None for {position_company}, skipping")
                            continue
                        
                        # 计算总月数
                        total_months = (current_year - start_year) * 12 + (current_month - start_month)
                        if total_months < 0:
                            total_months = 0
                        
                        # 转换为年/月格式
                        position_years = total_months // 12
                        position_months = total_months % 12
                        year_month_at_position = f"{position_years} years {position_months} months"
                        
                        # 添加当前职位到时间范围列表
                        all_time_ranges.append({
                            'company': position_company,
                            'start_year': start_year,
                            'start_month': start_month,
                            'end_year': current_year,
                            'end_month': current_month,
                            'months': total_months
                        })
                        print(f"Added current position: {position_company} ({start_year}-{start_month:02d} to {current_year}-{current_month:02d}) = {total_months} months")
                            
                    except Exception as e:
                        print(f"Error processing position time: {e}, skipping position at {position_company}")
                        continue

                    # 🛡️ 安全字符串格式化
                    try:
                        current_position_summary += f"{position_title} at {position_company} for Industry {position_industry} for {year_month_at_position}. \n"
                        current_position_summary += f"{position_company} description: \n {company_desc or 'No description'} \n"
                        current_position_summary += f"In total at {position_company} for {year_month_at_company}. \n\n\n"
                    except Exception as e:
                        print(f"Error formatting current position summary: {e}")
                        current_position_summary += f"Position at {position_company or 'Unknown Company'}\n\n"

            # 3. Past position summary
            post_position_summary = ""
            if past_positions:
                for position in past_positions:
                    if not isinstance(position, dict):
                        continue

                    try:
                        start_on = position.get("startDateOn")
                        end_on = position.get("endDateOn")
                        position_title = position.get("title", "Unknown Title")
                        position_company = position.get("companyName", "Unknown Company")

                        # 🛡️ 安全处理可能为None的日期字段
                        if start_on is not None:
                            try:
                                # 解析开始时间
                                start_year = start_on.get("year", 0)
                                start_month = start_on.get("month", 1)
                                
                                # 确保start_year不是None，month不知道默认为1
                                if start_year is None:
                                    print(f"Warning: start_year is None for past position {position_company}, skipping")
                                    year_month_at_position = "Unknown duration"
                                    post_position_summary += f"\n\n{position_title} at {position_company} {year_month_at_position}\n\n"
                                    continue
                                
                                # 如果没有开始年份，跳过这个position
                                if start_year == 0:
                                    print(f"No valid start year in past position, skipping: {position_company}")
                                    year_month_at_position = "Unknown duration"
                                    post_position_summary += f"\n\n{position_title} at {position_company} {year_month_at_position}\n\n"
                                    continue
                                
                                # 处理结束时间：如果end_on为None，使用当前时间
                                if end_on is not None:
                                    end_year = end_on.get("year", 0)
                                    end_month = end_on.get("month", 1)
                                else:
                                    # 使用当前时间作为结束时间
                                    from datetime import datetime
                                    current_date = datetime.now()
                                    end_year = current_date.year
                                    end_month = current_date.month
                                    print(f"endDateOn is None, using current time for {position_company}: {end_year}-{end_month:02d}")
                                
                                # 如果end_year为None，使用当前时间
                                if end_year is None:
                                    from datetime import datetime
                                    current_date = datetime.now()
                                    end_year = current_date.year
                                    end_month = current_date.month
                                    print(f"end_year is None, using current time for {position_company}: {end_year}-{end_month:02d}")
                                # 如果没有结束年份，使用当前时间
                                elif end_year == 0:
                                    from datetime import datetime
                                    current_date = datetime.now()
                                    end_year = current_date.year
                                    end_month = current_date.month
                                    print(f"end_year is 0, using current time for {position_company}: {end_year}-{end_month:02d}")
                                
                                # 计算总月数
                                total_months = (end_year - start_year) * 12 + (end_month - start_month)
                                print(f"Debug: {position_company} - start: {start_year}-{start_month:02d}, end: {end_year}-{end_month:02d}, total_months: {total_months}")
                                if total_months < 0:
                                    total_months = 0
                                    print(f"Warning: Negative total_months set to 0 for {position_company}")
                                
                                # 转换为年/月格式
                                tenure_years = total_months // 12
                                tenure_months = total_months % 12
                                year_month_at_position = f"{tenure_years} years {tenure_months} months"
                                
                                # 添加过去职位到时间范围列表
                                all_time_ranges.append({
                                    'company': position_company,
                                    'start_year': start_year,
                                    'start_month': start_month,
                                    'end_year': end_year,
                                    'end_month': end_month,
                                    'months': total_months
                                })
                                print(f"Added past position: {position_company} ({start_year}-{start_month:02d} to {end_year}-{end_month:02d}) = {total_months} months")
                                if tenure_years is not None and tenure_months is not None:
                                    year_month_at_position = f"{tenure_years} years {tenure_months} months"
                                else:
                                    year_month_at_position = "Unknown duration"
                            except Exception as e:
                                print(f"Error calculating date difference: {e}")
                                year_month_at_position = "Unknown duration"
                        else:
                            # 如果start_on为None，跳过这个position
                            print(f"startDateOn is None, skipping position: {position_company}")
                            year_month_at_position = "Unknown duration"
                            post_position_summary += f"\n\n{position_title} at {position_company} {year_month_at_position}\n\n"
                            continue

                        post_position_summary += f"\n\n{position_title} at {position_company} {year_month_at_position}\n\n"

                    except Exception as e:
                        print(f"Error processing past position: {e}")
                        # 尝试添加基本信息
                        title = position.get("title", "Unknown Title")
                        company = position.get("companyName", "Unknown Company")
                        post_position_summary += f"\n\n{title} at {company}\n\n"

            # 合并重叠的时间区间
            def merge_intervals(intervals):
                if not intervals:
                    return []
                
                # 按开始时间排序
                intervals.sort(key=lambda x: (x['start_year'], x['start_month']))
                
                merged = [intervals[0]]
                
                for current in intervals[1:]:
                    last = merged[-1]
                    
                    # 检查是否有重叠
                    if (current['start_year'] < last['end_year'] or 
                        (current['start_year'] == last['end_year'] and current['start_month'] <= last['end_month'])):
                        # 有重叠，合并区间
                        last['end_year'] = max(last['end_year'], current['end_year'])
                        last['end_month'] = max(last['end_month'], current['end_month'])
                        # 重新计算月数
                        last['months'] = (last['end_year'] - last['start_year']) * 12 + (last['end_month'] - last['start_month'])
                        print(f"Merged overlapping intervals: {last['company']} + {current['company']} -> {last['start_year']}-{last['start_month']:02d} to {last['end_year']}-{last['end_month']:02d}")
                    else:
                        # 无重叠，添加新区间
                        merged.append(current)
                
                return merged
            
            # 合并所有时间区间
            merged_ranges = merge_intervals(all_time_ranges)
            
            # 计算总经验月数
            total_experience_months = sum(range_info['months'] for range_info in merged_ranges)
            
            print(f"Total experience calculation:")
            print(f"  - Raw intervals: {len(all_time_ranges)}")
            print(f"  - Merged intervals: {len(merged_ranges)}")
            print(f"  - Total experience: {total_experience_months} months")

            total_experience_years = total_experience_months // 12

            # 根据参数决定返回值
            if include_education_summary:
                return (
                    linkedin_id or "Unknown",
                    first_name,
                    last_name,
                    degree,
                    location,
                    person_summary or "",
                    current_position_summary,
                    post_position_summary,
                    total_experience_years,
                    education_summary,  # 额外返回完整教育背景
                    open_to_opportunities  # memberPreferences.openToNewOpportunities
                )
            else:
                # 默认返回（向后兼容）
                return (
                    linkedin_id or "Unknown",
                    first_name,
                    last_name,
                    degree,
                    location,
                    person_summary or "",
                    current_position_summary,
                    post_position_summary,
                    total_experience_years
                )

        except:
            pass

    elif channel == "sales_nav":
        try:
            # 🛡️ 安全提取基础信息
            raw_linkedin_id = job_info.get("entityUrn", "")
            if not raw_linkedin_id:
                print("Warning: Missing entityUrn in job_info")
                return None

            linkedin_id = extract_linkedin_urn(raw_linkedin_id, channel="sales_nav")
            first_name = job_info.get("firstName", "Unknown")
            last_name = job_info.get("lastName", "Unknown")
            degree = job_info.get("degree", -1)
            location = job_info.get("geoRegion", "Unknown Location")
            person_summary = job_info.get("summary", "")
            past_positions = job_info.get("pastPositions", [])
            current_position = job_info.get("currentPositions", [])

            # 🛡️ 确保positions是列表类型
            if not isinstance(past_positions, list):
                past_positions = []
            if not isinstance(current_position, list):
                current_position = []

            # 2. Current position summary
            total_experience_months = 0
            current_position_summary = ""
            if current_position:
                for position in current_position:
                    if not isinstance(position, dict):
                        continue

                    position_company = position.get("companyName", "Unknown Company")
                    company_desc = position.get("description", "")
                    position_industry = position.get("industry", "Unknown Industry")
                    position_title = position.get("title", "Unknown Title")

                    # 🛡️ 安全获取tenure信息，防止None错误
                    try:
                        tenure_at_company = position.get("tenureAtCompany") or {}
                        if isinstance(tenure_at_company, dict):
                            tenure_years = tenure_at_company.get("numYears", 0)
                            tenure_months = tenure_at_company.get("numMonths", 0)
                        else:
                            tenure_years = tenure_months = 0

                        total_experience_months += tenure_years * 12 + tenure_months
                        year_month_at_company = f"{tenure_years} years {tenure_months} months"
                    except Exception as e:
                        print(f"Error processing tenureAtCompany: {e}")
                        year_month_at_company = "Unknown duration"

                    try:
                        tenure_at_position = position.get("tenureAtPosition") or {}
                        if isinstance(tenure_at_position, dict):
                            tenure_years = tenure_at_position.get("numYears", 0)
                            tenure_months = tenure_at_position.get("numMonths", 0)
                        else:
                            tenure_years = tenure_months = 0
                        year_month_at_position = f"{tenure_years} years {tenure_months} months"
                    except Exception as e:
                        print(f"Error processing tenureAtPosition: {e}")
                        year_month_at_position = "Unknown duration"

                    # 🛡️ 安全字符串格式化
                    try:
                        current_position_summary += f"{position_title} at {position_company} for Industry {position_industry} for {year_month_at_position}. \n"
                        current_position_summary += f"{position_company} description: \n {company_desc or 'No description'} \n"
                        current_position_summary += f"In total at {position_company} for {year_month_at_company}. \n\n\n"
                    except Exception as e:
                        print(f"Error formatting current position summary: {e}")
                        current_position_summary += f"Position at {position_company or 'Unknown Company'}\n\n"

            # 3. Past position summary
            post_position_summary = ""
            if past_positions:
                for position in past_positions:
                    if not isinstance(position, dict):
                        continue

                    try:
                        start_on = position.get("startedOn")
                        end_on = position.get("endedOn")
                        position_title = position.get("title", "Unknown Title")
                        position_company = position.get("companyName", "Unknown Company")

                        # 🛡️ 安全处理可能为None的日期字段
                        if start_on is not None and end_on is not None:
                            try:
                                _, tenure_years, tenure_months = year_month_diff(start_on, end_on)
                                total_experience_months += tenure_years * 12 + tenure_months
                                if tenure_years is not None and tenure_months is not None:
                                    year_month_at_position = f"{tenure_years} years {tenure_months} months"
                                else:
                                    year_month_at_position = "Unknown duration"
                            except Exception as e:
                                print(f"Error calculating date difference: {e}")
                                year_month_at_position = "Unknown duration"
                        else:
                            year_month_at_position = "Unknown duration"

                        post_position_summary += f"\n\n{position_title} at {position_company} {year_month_at_position}\n\n"

                    except Exception as e:
                        print(f"Error processing past position: {e}")
                        # 尝试添加基本信息
                        title = position.get("title", "Unknown Title")
                        company = position.get("companyName", "Unknown Company")
                        post_position_summary += f"\n\n{title} at {company}\n\n"

            total_experience_years = total_experience_months // 12

            return (
                linkedin_id or "Unknown",
                first_name,
                last_name,
                degree,
                location,
                person_summary or "",
                current_position_summary,
                post_position_summary,
                total_experience_years
            )

        except Exception as e:
            print(f"Critical error in extract_candidate_info: {e}")
            print(f"job_info keys: {list(job_info.keys()) if isinstance(job_info, dict) else 'Not a dict'}")
            # 返回安全的默认值
            return (
                "error_profile",
                "Unknown",
                "Unknown",
                -1,
                "Unknown Location",
                "Profile extraction failed due to data format issues",
                "Unable to extract current position information",
                "Unable to extract past position information",
            )
    else:
        raise ValueError("Searching channel name is not allowed: {}".format(channel))



def improved_linkedin_search_api(
    linkedin_ids, max_wait_time=7200, auto_trigger_after=600, check_interval=1
):
    """
    Performs an improved search operation on LinkedIn profiles by utilizing the
    LinkedInIntegrationService. The function automates the workflow to crawl
    and query LinkedIn-based profiles, processes search requests, and retrieves
    associated information about the profiles.

    The operation includes an automated waiting mechanism with configurable
    parameters for maximum wait time, auto trigger time, and status check
    interval. Upon completion, it successfully fetches task details and profile
    information.

    :param linkedin_ids: List of LinkedIn IDs to be queried.
    :type linkedin_ids: list[str]

    :return: A tuple containing the list of profiles retrieved and job details
             including the task UUID and the count of profiles found.
    :rtype: tuple[list[dict], tuple[str, int]]
    """

    service = LinkedInIntegrationService()
    result = service.crawl_and_query_workflow(
        linkedin_ids=linkedin_ids,
        max_wait_time=max_wait_time,  # 最多等待2小时，超过后自动退出
        auto_trigger_after=auto_trigger_after,  # 10分钟后自动触发插队处理
        check_interval=check_interval,  # 每秒检查状态
    )

    # 获取结果
    task_uuid = result["summary"]["task_uuid"]
    profiles_count = result["summary"]["profiles_found"]
    profiles = result["profiles"]["data"]["profiles"]

    print(f"任务完成! UUID: {task_uuid}")
    print(f"找到 {profiles_count} 个LinkedIn profiles")
    print(f"是否手动触发过: {'是' if result['summary']['manual_triggered'] else '否'}")
    print("profiles:", profiles)
    succeed = profiles_count > 0
    job_info = (task_uuid, profiles_count)

    return profiles, succeed, job_info


def format_position_desc(experiences):
    job_summary = ""
    for position in experiences:
        position_company = position.get("company")
        position_title = position.get("title")
        position_desc = position.get("description")

        start_on = position.get("startDate")
        end_on = position.get("endDate", None)
        end_on = end_on if end_on else datetime.now().strftime("%Y-%m-%d")

        def years_months_duration(start_date: str, end_date: str):
            """
            Return the elapsed whole years and months between two YYYY-MM-DD dates.

            Example
            -------
            >>> years, months = years_months_duration("2023-05-01", "2025-03-09")
            >>> print(years, months)      # 1 year, 10 months (extra days are ignored)
            1 10
            """
            if end_date is None:
                end_date = datetime.now().strftime("%Y-%m-%d")

            try:
                start = datetime.strptime(start_date, "%Y-%m-%d")
                end = datetime.strptime(end_date, "%Y-%m-%d")
            except Exception as e:
                print(f"Error parsing dates: {e}")
                return None, None

            delta = relativedelta(end, start)  # gives years, months, days, …

            return delta.years, delta.months

        tenure_years, tenure_months = years_months_duration(start_on, end_on)
        year_month_at_position = f"{tenure_years} years {tenure_months} months"
        job_summary += f"{position_title} at {position_company} {year_month_at_position} from {start_on} to {end_on}\n"

    return job_summary


def format_education_desc(educations):
    job_summary = ""
    for education in educations:
        school = education.get("collegeName")
        degree = education.get("degreeName")
        major = education.get("majorName")

        start_on = education.get("startDate")
        end_on = education.get("endDate", None)
        end_on = end_on if end_on else datetime.now().strftime("%Y-%m-%d")

        job_summary += f"{degree} for {major} at {school} from {start_on} to {end_on}\n"

    return job_summary


def extract_fixed_person_info(raw_search_results):
    """
    Fixes and formats person's experience and education information extracted from raw search results.

    The function processes each record in the provided raw search results, formatting
    the description of the current job experiences, previous job experiences, and
    educational background. Each processed record is consolidated into a DataFrame
    with specified columns.

    :param raw_search_results: List of candidate records where each record contains
        information about experiences and educations. Each dictionary in the list
        should include keys 'experiences' and 'educations'.

    :return: A Pandas DataFrame containing fixed and formatted summaries of current
        job experiences, previous job experiences, and educational background. Columns
        in the DataFrame are "cur_job_summary", "prev_job_summary", and "education_summary".
    """
    fixed_person_info = []
    for candidate_idx, record in enumerate(raw_search_results):
        linkedin_id = record["platform_id"]
        print("candidate_idx:", candidate_idx)
        experiences = record["experiences"]
        educations = record["educations"]
        print("experiences_num:", len(experiences))

        cur_job_exp = [experiences[0]] if len(experiences) > 0 else ""


        prev_job_exps = experiences[1:] if len(experiences) > 1 else ""

        fixed_cur_job_summary = format_position_desc(cur_job_exp)
        fixed_prev_job_summary = format_position_desc(prev_job_exps)
        fixed_education_summary = format_education_desc(educations)
        fixed_person_info.append(
            [
                linkedin_id,
                fixed_cur_job_summary,
                fixed_prev_job_summary,
                fixed_education_summary,
            ]
        )

    fixed_person_info_df = pd.DataFrame(
        fixed_person_info,
        columns=[
            "linkedin_id",
            "cur_job_summary",
            "prev_job_summary",
            "education_summary",
        ],
    )

    return fixed_person_info_df


if __name__ == "__main__":
    # raw_filters = {
    #     "job_function": ["Engineering"],
    #     "seniority": ["Senior", "Strategic"],
    #     "industry": ["Software Development"],
    #     "job_title": ["ML Engineer", "AI Engineer", "Vision Engineer"],
    #     "companies": [{"company_name": "Google"}, {"company_name": "Microsoft"}],
    #     "location": {"name": ["San Francisco Bay Area"]},
    # }
    # job_skills_str = ["Golang", "Distributed Systems", "Workflow Automation"]
    # search_results = search_linkedin(raw_filters, job_skills_str, start_id="0")
    # print(search_results)
    from utils.recruiter_api_formatter import convert_filters_to_recruiter_api_conditions
    from linkedin_recruiter_apiservice.api_service import RecruiterService
    from config.linkedin_enums import get_linkedin_enum_data
    from llms.chatgpt import ChatGPTWrapper

    rs = RecruiterService()
    filters1 = {
        # "filters": {
        #     "companies": ["Google"],
        #     # "job_title": ["AAA"],#["Software Engineer", "Data Scientist"],
        #     "location": {"name": ["San Francisco", "New York"]},
        #     "job_function": ["Engineering"],
        #     "industry": ["Software Development"],
        #     "language": ["chinese", "English"]

        "filters": {'job_function': ['Engineering'], 'seniority': ['entry level', 'senior'],
                      'industry': ['Software Development'], 'job_title': ['Backend Engineer', 'Backend Developer'],
                      'location': {'name': ['United States']}, 'year_of_experience': {'min': None, 'max': None}}
    }
    skills1 = ['Systems Design', 'Cloud Infrastructure']
    # skills1 = []
    llm = ChatGPTWrapper()

    linkedin_enum_params = get_linkedin_enum_data()
    #format_conditions = convert_filters_to_recruiter_api_conditions(filters1, skills1, linkedin_enum_params, llm)
    format_conditions = {
    "filters": {
      "locations": [
        {
          "name": "New York City Metropolitan Area",
          "required": False,
          "selected": True,
          "negated": False
        },
        {
          "name": "Greater Chicago Area",
          "required": False,
          "selected": True,
          "negated": False
        },
        {
          "name": "Charleston, South Carolina, United States",
          "required": False,
          "selected": True,
          "negated": False
        }
      ],
      "skills": [
        {
          "name": "High-Frequency Trading",
          "required": False,
          "selected": True,
          "negated": False
        },
        {
          "name": "Low Latency Optimization",
          "required": False,
          "selected": True,
          "negated": False
        },
        {
          "name": "Market Data Interfaces",
          "required": False,
          "selected": True,
          "negated": False
        }
      ],
      "industries": [
        {
          "name": "Financial Services",
          "required": False,
          "selected": True,
          "negated": False
        },
        {
          "name": "IT Services and IT Consulting",
          "required": False,
          "selected": True,
          "negated": False
        },
        {
          "name": "Software Development",
          "required": False,
          "selected": True,
          "negated": False
        }
      ],
      "job_functions": [
        "Engineering"
      ],
      "titles": [
        {
          "name": "Software Engineer",
          "time_scope": "CURRENT",
          "required": False,
          "negated": False
        },
        {
          "name": "Quantitative Developer",
          "time_scope": "CURRENT",
          "required": False,
          "negated": False
        }
      ],
      "languages": [
        {
          "name": "Chinese",
          "language_proficiency_scope": "NATIVE_OR_BILINGUAL",
          "required": False,
          "selected": True,
          "negated": False
        }
      ]
    }}
    
    search_results_num = rs.get_search_num(format_conditions)["num"]
    print(f"search_results_num: {search_results_num}")
    job_main_skills=[
        "High-Frequency Trading",
        "Low Latency Optimization",
        "Market Data Interfaces"
    ]
    final_search_results = batch_basic_linkedin_search(
            search_results_num = search_results_num,
            format_filter_conditions = format_conditions,
            job_main_skills = job_main_skills,
            records_per_search=25,
            max_search_num=500,
            channel="recruiter")
   

    # import json

    # last_ccd = None
    # for ccd in final_search_results:
    #     e_info = extract_candidate_info(ccd)
    #     last_ccd = ccd  # 记录最后一个候选人

    # # 保存最后一个候选人到 JSON 文件
    # if last_ccd is not None:
    #     output_file = "last_candidate.json"
    #     with open(output_file, 'w', encoding='utf-8') as f:
    #         json.dump(last_ccd, f, ensure_ascii=False, indent=2)
    #     print(f"\n✅ 最后一个候选人已保存到: {output_file}")
    print(f"result number: {len(final_search_results)}")