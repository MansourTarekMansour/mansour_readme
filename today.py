import datetime
from dateutil import relativedelta
import requests
import os
from xml.dom import minidom
import time
import hashlib

# Personal access token with permissions: read:enterprise, read:org, read:repo_hook, read:user, repo
HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME'] # 'Andrew6rant'
QUERY_COUNT = {'user_id_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def daily_readme(birthday):
    """
    Returns the length of time since I was born
    e.g. 'XX years, XX months, XX days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}'.format(
        diff.years, 'year' + format_plural(diff.years), 
        diff.months, 'month' + format_plural(diff.months), 
        diff.days, 'day' + format_plural(diff.days))


def format_plural(unit):
    """
    Returns a properly formatted number
    e.g.
    'day' + format_plural(diff.days) == 5
    >>> '5 days'
    'day' + format_plural(diff.days) == 1
    >>> '1 day'
    """
    return 's' if unit != 1 else ''


def graph_commits(start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])
    raise Exception('The request has failed, graph_commits()')


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])
    raise Exception('The request has failed, graph_repos_stars()')


def recursive_loc(owner, repo_name, addition_total=0, deletion_total=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] != None: # Only count commits if repo isn't empty
            return loc_counter_one_repo(owner, repo_name, request.json()['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total)
        else: return 0
    elif request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    print(request.status_code)
    raise Exception('The request has failed, recursive_loc()')


def loc_counter_one_repo(owner, repo_name, history, addition_total, deletion_total):
    """
    Recursively call recursive_loc (since GraphQL can only search 100 commits at a time) 
    only adds the LOC value of commits authored by me
    """
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total
    else: return recursive_loc(owner, repo_name, addition_total, deletion_total, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, cursor=None):
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        return loc_counter(request.json()['data']['user']['repositories']['edges'])
    raise Exception('The request has failed, loc_query()')

def loc_counter(edges, loc_add=0, loc_del=0):
    """
    Checks every repository to see if it has been updated since the last time it was cached
    If it has, run recursive_loc on that repository to update the LOC count
    """
    with open('cache.txt', 'r') as f:
        data = f.readlines()
    if len(data)-6 != len(edges):
        flush_cache(edges)
        with open('cache.txt', 'r') as f:
            data = f.readlines()
    cache_comment = data[:6] # save the first 6 lines
    data = data[6:] # remove those lines
    for index in range(len(edges)):
        repo_hash, commit_count, __, __ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            if int(commit_count) != edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']:
                # commit count has changed, update cache
                owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                loc = recursive_loc(owner, repo_name)
                data[index] = repo_hash + ' ' + str(edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n'
    with open('cache.txt', 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[2])
        loc_del += int(loc[3])
    return [loc_add, loc_del, loc_add - loc_del]


def flush_cache(edges):
    """
    Wipes the cache file
    This is called when the number of repositories changes
    """
    with open('cache.txt', 'r') as f:
        data = f.readlines()
        data = data[:6] # only save the first 6 lines
    with open('cache.txt', 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0\n')


def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, loc_data):
    """
    Parse SVG files and update elements with my age, commits, stars, repositories, and lines written
    """
    svg = minidom.parse(filename)
    f = open(filename, mode='w', encoding='utf-8')
    tspan = svg.getElementsByTagName('tspan')
    tspan[30].firstChild.data = age_data
    tspan[65].firstChild.data = repo_data
    tspan[67].firstChild.data = contrib_data
    tspan[69].firstChild.data = commit_data
    tspan[71].firstChild.data = star_data
    tspan[73].firstChild.data = loc_data[2]
    tspan[74].firstChild.data = loc_data[0] + '++'
    tspan[75].firstChild.data = loc_data[1] + '--'
    f.write(svg.toxml('utf-8').decode('utf-8'))
    f.close()


def commit_counter(date):
    """
    Counts up my total commits.
    Loops commits per year (starting backwards from today, continuing until my account's creation date)
    """
    total_commits = 0
    # since GraphQL's contributionsCollection has a maximum reach of one year
    while date.isoformat() > '2019-11-02T00:00:00.000Z': # one day before my very first commit
        old_date = date.isoformat()
        date = date - datetime.timedelta(days=365)
        total_commits += graph_commits(date.isoformat(), old_date)
    return total_commits


def svg_element_getter(filename):
    """
    Prints the element index of every element in the SVG file
    """
    svg = minidom.parse(filename)
    open(filename, mode='r', encoding='utf-8')
    tspan = svg.getElementsByTagName('tspan')
    for index in range(len(tspan)): print(index, tspan[index].firstChild.data)


def user_id_getter(username):
    """
    Returns the account ID of the username
    """
    query_count('user_id_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
        }
    }'''
    variables = {'login': username}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        return {'id': request.json()['data']['user']['id']}
    raise Exception('The request has failed, user_id_getter()')


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, query_type, whitespace, *args):
    """
    Prints the time it takes for a function to run
    Returns the function's result and time differential if whitespace argument is -1, otherwise returns formatted result and time differential
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    difference = time.perf_counter() - start
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    return (f"{'{:,}'.format(funct_return): <{whitespace}}", difference) if whitespace >= 0 else (funct_return, difference)


if __name__ == '__main__':
    """
    Runs program over each SVG image
    """
    print('Calculation times:')
    # define global variable for owner ID
    # e.g {'id': 'MDQ6VXNlcjU3MzMxMTM0'} for username 'Andrew6rant'
    OWNER_ID, id_time = perf_counter(user_id_getter, 'owner id', -1, USER_NAME)
    
    age_data, age_time = perf_counter(daily_readme, 'age calculation', -1, datetime.datetime(2002, 7, 5))
    total_loc, loc_time = perf_counter(loc_query, 'LOC (cached)', -1, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    commit_data, commit_time = perf_counter(commit_counter, 'commit counter', 7, datetime.datetime.today())
    star_data, star_time = perf_counter(graph_repos_stars, 'star counter', 0, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'my repositories', 2, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'contributed repos', 2, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])

    for index in range(len(total_loc)): total_loc[index] = '{:,}'.format(total_loc[index]) # format added, deleted, and total LOC

    svg_overwrite('dark_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, total_loc)
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, total_loc)

    # move cursor to override 'Calculation times:' with 'Total function time:' and the total function time, then move cursor back
    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
        '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % (id_time + age_time + loc_time + commit_time + star_time + repo_time + contrib_time)),
        ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
